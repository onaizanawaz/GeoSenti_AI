"""Graph generation.

Three planners behind one entry point, chosen by PLANNER_MODE:

  static  -- the hand-written water-stress graph (default; no LLM needed)
  llm     -- an LLM emits the graph, which is validated and repaired
  dummy   -- the hidden test chain, for orchestration tests without GEE

The LLM never reaches the executor unchecked. Its output goes through the same
validate_graph() the API uses, and failures are handed back as a repair prompt.
After llm_max_repairs attempts the planner falls back to the static graph
rather than failing the request: a working approximation beats a 500.

Validation for LLM output uses the VISIBLE registry only, so a hallucinated
"dummy_source" is rejected as an unknown node type instead of quietly running
the test nodes.
"""

from __future__ import annotations

import json
import logging
import re

from app.config import get_settings
from app.schemas import Edge, NodeSpec, WorkflowGraph
from app.services.dag import blocking, validate_graph
from app.services.llm.prompt import build_repair, build_system, build_user
from app.services.nodes import load_registry

log = logging.getLogger(__name__)


class PlannerError(RuntimeError):
    pass


def _edges(pairs) -> list[Edge]:
    return [Edge(source=s, target=t) for s, t in pairs]


# ------------------------------------------------------------ static planners

def generate_graph_dummy(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    """Three-node chain over the hidden test nodes. Runs without GEE."""
    nodes = [
        NodeSpec(id="n1", type="dummy_source", params={"value": 3},
                 inputs=[], outputs=["dummy_a"]),
        NodeSpec(id="n2", type="dummy_transform", params={},
                 inputs=["dummy_a"], outputs=["dummy_b"]),
        NodeSpec(id="n3", type="dummy_sink", params={},
                 inputs=["dummy_b"], outputs=["dummy_out"]),
    ]
    return WorkflowGraph(workflow_id="stub", nodes=nodes,
                         edges=_edges([("n1", "n2"), ("n2", "n3")]))


def generate_graph_water_stress(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    """Sentinel-2 indices + WaPOR ET -> a water stress classification."""
    nodes = [
        NodeSpec(id="n1", type="fetch_sentinel2", params={"max_cloud_pct": 60},
                 inputs=[], outputs=["raw_s2"]),
        NodeSpec(id="n2", type="cloud_mask", params={"cloud_prob_thresh": 40},
                 inputs=["raw_s2"], outputs=["clean_s2"]),
        NodeSpec(id="n3", type="compute_ndvi", params={"reducer": "median"},
                 inputs=["clean_s2"], outputs=["ndvi"]),
        NodeSpec(id="n4", type="compute_ndmi", params={"reducer": "median"},
                 inputs=["clean_s2"], outputs=["ndmi"]),
        NodeSpec(id="n5", type="fetch_wapor_et", params={},
                 inputs=[], outputs=["et"]),
        NodeSpec(id="n6", type="water_stress_classify",
                 params={"ndmi_thresh": 0.2, "et_deficit_thresh": 0.3, "mode": "auto"},
                 inputs=["ndvi", "ndmi", "et"],
                 outputs=["stress_class", "stress_stats"]),
    ]
    return WorkflowGraph(workflow_id="stub", nodes=nodes, edges=_edges(
        [("n1", "n2"), ("n2", "n3"), ("n2", "n4"),
         ("n3", "n6"), ("n4", "n6"), ("n5", "n6")]))


# --------------------------------------------------------------- LLM planner

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply.

    Small local models ignore response_format and wrap output in fences or add
    a sentence of preamble, so the parser handles that rather than failing the
    whole plan over punctuation.
    """
    if not text or not text.strip():
        raise PlannerError("Model returned an empty response.")

    candidates = [m.strip() for m in _FENCE.findall(text)]
    candidates.append(text.strip())

    # Last resort: the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for c in candidates:
        try:
            parsed = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise PlannerError(f"No JSON object found in model reply: {text[:300]}")


def graph_from_payload(payload: dict) -> WorkflowGraph:
    """Coerce a raw model payload into a WorkflowGraph.

    Tolerant about shape but not about content: missing keys become empty
    lists so validate_graph() can report a precise error, instead of a
    KeyError surfacing as a 500.
    """
    nodes = [
        NodeSpec(
            id=str(n.get("id", "")),
            type=str(n.get("type", "")),
            params=n.get("params") or {},
            inputs=list(n.get("inputs") or []),
            outputs=list(n.get("outputs") or []),
        )
        for n in (payload.get("nodes") or [])
        if isinstance(n, dict)
    ]
    edges = [
        Edge(
            source=str(e.get("source", "")),
            target=str(e.get("target", "")),
            source_output=e.get("source_output"),
            target_input=e.get("target_input"),
        )
        for e in (payload.get("edges") or [])
        if isinstance(e, dict)
    ]
    return WorkflowGraph(workflow_id="stub", nodes=nodes, edges=edges)


def generate_graph_llm(query: str, aoi: dict, date_range: dict,
                       client=None) -> WorkflowGraph:
    """Plan a graph with the LLM, repairing it against the validator.

    client is injectable so the repair loop can be tested without a server.
    Raises PlannerError if no valid graph survives the repair budget.
    """
    if client is None:
        from app.services.llm.client import get_client
        client = get_client()

    s = get_settings()
    registry = load_registry(include_hidden=False)
    system = build_system()
    user = build_user(query, aoi, date_range)

    last_errors = None
    for attempt in range(1, s.llm_max_repairs + 2):     # first try + repairs
        reply = client.complete(system, user)
        try:
            graph = graph_from_payload(extract_json(reply))
        except PlannerError as e:
            # Unparseable is just another validation failure to feed back.
            last_errors = [_synthetic_error("unparseable", str(e))]
            user = build_repair(reply[:2000], last_errors)
            log.warning("Planner attempt %d: %s", attempt, e)
            continue

        errors = blocking(validate_graph(graph, registry))
        if not errors:
            log.info("Planner produced a valid graph on attempt %d "
                     "(%d nodes)", attempt, len(graph.nodes))
            return graph

        last_errors = errors
        log.warning("Planner attempt %d rejected: %s", attempt,
                    "; ".join(f"[{e.code}] {e.message}" for e in errors))
        user = build_repair(json.dumps(graph.model_dump(), indent=1), errors)

    detail = "; ".join(f"[{e.code}] {e.message}" for e in (last_errors or []))
    raise PlannerError(
        f"No valid graph after {s.llm_max_repairs + 1} attempts. Last errors: {detail}")


class _synthetic_error:
    """Shaped like a GraphError so build_repair() can render it uniformly."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        self.node_id = None
        self.hint = "Return only a single JSON object."


# ---------------------------------------------------------------- entry point

def generate_graph(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    mode = (get_settings().planner_mode or "static").lower()

    if mode == "dummy":
        return generate_graph_dummy(query, aoi, date_range)

    if mode == "llm":
        try:
            return generate_graph_llm(query, aoi, date_range)
        except Exception as e:                  # noqa: BLE001 - includes LLMError
            # Falling back beats a 500: the user gets a working water-stress
            # graph, and the reason is in the logs rather than swallowed.
            log.error("LLM planner failed (%s); falling back to the static "
                      "water-stress graph.", e)

    return generate_graph_water_stress(query, aoi, date_range)