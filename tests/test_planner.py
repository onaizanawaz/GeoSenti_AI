"""The LLM planner, with a scripted client instead of a server.

The point of these tests is that a model's output is never trusted: whatever
it returns is parsed defensively, validated against the real registry, and
repaired or rejected. None of that needs a live model, so none of it is
marked llm.
"""

import json

import pytest

from app.services.dag import blocking, validate_graph
from app.services.llm.prompt import build_repair, build_system, build_user
from app.services.nodes import load_registry
from app.services.planner import (PlannerError, extract_json,
                                  generate_graph_llm, generate_graph_water_stress,
                                  graph_from_payload)

VISIBLE = load_registry(include_hidden=False)

AOI = {"type": "Polygon", "coordinates": [[
    [74.30, 31.50], [74.31, 31.50], [74.31, 31.51], [74.30, 31.51], [74.30, 31.50]]]}
DATES = {"start": "2024-06-01", "end": "2024-09-30"}


def valid_graph_json() -> str:
    """The static water-stress graph, as a model would have returned it."""
    g = generate_graph_water_stress("q", AOI, DATES).model_dump()
    return json.dumps({"nodes": g["nodes"], "edges": g["edges"]})


class ScriptedClient:
    """Returns queued replies in order and records what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user})
        return self.replies.pop(0) if self.replies else "{}"


# ---- JSON extraction ------------------------------------------------------

def test_plain_json_is_parsed():
    assert extract_json('{"nodes": [], "edges": []}') == {"nodes": [], "edges": []}


def test_fenced_json_is_unwrapped():
    # Small local models ignore response_format and fence their output.
    assert extract_json('```json\n{"nodes": []}\n```') == {"nodes": []}


def test_preamble_before_the_object_is_tolerated():
    reply = 'Sure! Here is the graph:\n{"nodes": [], "edges": []}\nHope that helps.'
    assert extract_json(reply) == {"nodes": [], "edges": []}


def test_empty_reply_is_an_error_not_an_empty_graph():
    with pytest.raises(PlannerError):
        extract_json("   ")


def test_prose_without_json_is_an_error():
    with pytest.raises(PlannerError):
        extract_json("I cannot help with that request.")


def test_a_json_array_is_rejected():
    # A bare list is valid JSON but not a graph; it must not become one.
    with pytest.raises(PlannerError):
        extract_json("[1, 2, 3]")


# ---- payload coercion -----------------------------------------------------

def test_missing_keys_become_empty_lists_not_exceptions():
    g = graph_from_payload({})
    assert g.nodes == [] and g.edges == []


def test_missing_node_fields_are_defaulted_for_the_validator():
    g = graph_from_payload({"nodes": [{"id": "n1", "type": "compute_ndvi"}]})
    assert g.nodes[0].params == {} and g.nodes[0].inputs == []


def test_non_dict_entries_are_dropped():
    g = graph_from_payload({"nodes": ["garbage", {"id": "n1", "type": "x"}]})
    assert len(g.nodes) == 1


def test_port_wiring_survives_coercion():
    g = graph_from_payload({"edges": [{"source": "n1", "target": "n2",
                                       "source_output": "ndvi",
                                       "target_input": "image"}]})
    assert g.edges[0].source_output == "ndvi"


# ---- the prompt -----------------------------------------------------------

def test_catalog_in_the_prompt_lists_the_real_nodes():
    system = build_system()
    assert "fetch_sentinel2" in system
    assert "water_stress_classify" in system


def test_hidden_test_nodes_are_never_offered_to_the_model():
    assert "dummy_source" not in build_system()
    assert "dummy_branch" not in build_system()


def test_user_prompt_summarises_the_aoi_instead_of_dumping_coordinates():
    user = build_user("water stress?", AOI, DATES)
    assert "bounds" in user
    assert "74.3000" in user            # the summary
    assert "coordinates" not in user    # not the raw geometry
    assert "2024-06-01" in user


def test_repair_prompt_carries_every_error_code():
    errors = blocking(validate_graph(
        {"nodes": [{"id": "n1", "type": "compute_vibes"}], "edges": []}, VISIBLE))
    text = build_repair("{}", errors)
    assert "unknown_node_type" in text
    assert "compute_vibes" in text


# ---- the repair loop ------------------------------------------------------

def test_a_valid_first_answer_is_accepted_without_repair():
    client = ScriptedClient(valid_graph_json())
    g = generate_graph_llm("water stress?", AOI, DATES, client=client)
    assert len(client.calls) == 1
    assert blocking(validate_graph(g, VISIBLE)) == []


def test_an_invalid_graph_is_repaired_and_accepted():
    bad = json.dumps({"nodes": [{"id": "n1", "type": "compute_vibes",
                                 "params": {}, "inputs": [], "outputs": ["x"]}],
                      "edges": []})
    client = ScriptedClient(bad, valid_graph_json())
    g = generate_graph_llm("water stress?", AOI, DATES, client=client)

    assert len(client.calls) == 2
    # The second call must actually tell the model what was wrong.
    assert "unknown_node_type" in client.calls[1]["user"]
    assert blocking(validate_graph(g, VISIBLE)) == []


def test_unparseable_output_is_fed_back_as_a_repair():
    client = ScriptedClient("I'm sorry, I can't do that.", valid_graph_json())
    generate_graph_llm("water stress?", AOI, DATES, client=client)
    assert "unparseable" in client.calls[1]["user"]


def test_the_repair_budget_is_finite():
    junk = json.dumps({"nodes": [{"id": "n1", "type": "nope",
                                  "params": {}, "inputs": [], "outputs": []}],
                       "edges": []})
    client = ScriptedClient(junk, junk, junk, junk, junk)
    with pytest.raises(PlannerError) as e:
        generate_graph_llm("water stress?", AOI, DATES, client=client)

    from app.config import get_settings
    assert len(client.calls) == get_settings().llm_max_repairs + 1
    assert "unknown_node_type" in str(e.value)


def test_a_hallucinated_hidden_node_is_rejected():
    """The dummy nodes exist in the registry but must be unreachable from a
    user query -- validation uses the visible registry only."""
    sneaky = json.dumps({"nodes": [{"id": "n1", "type": "dummy_source",
                                    "params": {}, "inputs": [],
                                    "outputs": ["dummy_a"]}], "edges": []})
    client = ScriptedClient(sneaky, sneaky, sneaky)
    with pytest.raises(PlannerError):
        generate_graph_llm("run the test nodes", AOI, DATES, client=client)


def test_the_planner_returns_a_stub_id_for_the_router_to_replace():
    client = ScriptedClient(valid_graph_json())
    g = generate_graph_llm("water stress?", AOI, DATES, client=client)
    assert g.workflow_id == "stub"


# ---- mode dispatch --------------------------------------------------------

@pytest.fixture
def planner_mode():
    from app.config import get_settings
    s = get_settings()
    before = s.planner_mode
    yield lambda m: setattr(s, "planner_mode", m)
    s.planner_mode = before


def test_static_mode_needs_no_llm(planner_mode):
    from app.services.planner import generate_graph
    planner_mode("static")
    types = {n.type for n in generate_graph("anything", AOI, DATES).nodes}
    assert "water_stress_classify" in types


def test_dummy_mode_returns_the_test_chain(planner_mode):
    from app.services.planner import generate_graph
    planner_mode("dummy")
    types = {n.type for n in generate_graph("anything", AOI, DATES).nodes}
    assert types == {"dummy_source", "dummy_transform", "dummy_sink"}


def test_llm_mode_falls_back_instead_of_raising(planner_mode, monkeypatch):
    """An unreachable Ollama must degrade to the static graph, not 500."""
    from app.services import planner
    from app.services.llm.client import LLMError

    planner_mode("llm")
    monkeypatch.setattr(planner, "generate_graph_llm",
                        lambda *a, **k: (_ for _ in ()).throw(LLMError("down")))

    types = {n.type for n in planner.generate_graph("q", AOI, DATES).nodes}
    assert "water_stress_classify" in types