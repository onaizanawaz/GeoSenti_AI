"""Live planner checks. Deselected by default via pytest.ini addopts.

Run once your provider is up:  pytest -m llm

These are the only tests that need a model. Everything about how the planner
handles model output is proven offline in test_planner.py.
"""

import pytest

from app.services.dag import blocking, validate_graph
from app.services.nodes import load_registry
from app.services.planner import generate_graph_llm

pytestmark = pytest.mark.llm

VISIBLE = load_registry(include_hidden=False)

AOI = {"type": "Polygon", "coordinates": [[
    [74.30, 31.50], [74.31, 31.50], [74.31, 31.51], [74.30, 31.51], [74.30, 31.50]]]}
DATES = {"start": "2024-06-01", "end": "2024-09-30"}


def test_provider_is_reachable_and_answers():
    from app.services.llm.client import get_client
    reply = get_client().complete(
        "You reply with JSON only.",
        'Return exactly: {"ok": true}')
    assert "ok" in reply.lower()


def test_the_model_plans_a_graph_the_validator_accepts():
    """The real bar for a local model: not "did it answer" but "did it answer
    with something the executor could actually run"."""
    graph = generate_graph_llm(
        "Is my wheat field under water stress this season?", AOI, DATES)
    assert blocking(validate_graph(graph, VISIBLE)) == []
    assert len(graph.nodes) >= 2


def test_a_narrower_question_produces_a_smaller_graph():
    """Rule 7 in the system prompt. A model that always emits the full
    water-stress graph regardless of the question is not planning."""
    ndvi_only = generate_graph_llm("Just show me NDVI for my field.", AOI, DATES)
    assert blocking(validate_graph(ndvi_only, VISIBLE)) == []
    types = {n.type for n in ndvi_only.nodes}
    assert "compute_ndvi" in types
    assert "fetch_wapor_et" not in types