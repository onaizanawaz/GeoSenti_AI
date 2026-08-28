"""Graph generation.

Still a stub: Phase 6 replaces generate_graph() with the LLM planner, which
will emit a graph constrained to the node catalog and repair it against
validate_graph() before returning.

Until Phase 3/4 registers the real GEE nodes, generate_graph() returns a
dummy chain so the API and orchestrator can be exercised end to end. The
water-stress graph below is the Phase 4 target and is already written against
the corrected catalog: inputs are ["ndvi", "ndmi", "et"], not the old
["ndvi", "ndmi", "et_raw"] which never matched the declared input schema.
"""

from app.schemas import Edge, NodeSpec, WorkflowGraph


def _edges(pairs) -> list[Edge]:
    return [Edge(source=s, target=t) for s, t in pairs]


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
    """The Phase 4 target graph. Validates only once the GEE nodes are registered."""
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


def generate_graph(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    # Phase 4 switches this to generate_graph_water_stress.
    return generate_graph_dummy(query, aoi, date_range)