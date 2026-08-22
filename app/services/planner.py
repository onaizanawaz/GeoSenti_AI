"""
Graph generation layer. Takes an NL query + AOI + date range and returns
a WorkflowGraph. For now this is a stub that returns a hardcoded graph
so the rest of the system (API, execution, frontend) can be built and
tested before the LLM call is wired in (see build plan, Phase 1 vs 2).
"""

from app.schemas import WorkflowGraph, NodeSpec


def generate_graph_stub(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    """Hardcoded water-stress workflow, used until the LLM planner is wired in."""
    nodes = [
        NodeSpec(id="n1", type="fetch_sentinel2", params={"bands": ["B4", "B8", "B11"]}, outputs=["raw_s2"]),
        NodeSpec(id="n2", type="cloud_mask", params={"max_cloud_pct": 20}, inputs=["raw_s2"], outputs=["clean_s2"]),
        NodeSpec(id="n3", type="compute_ndvi", inputs=["clean_s2"], outputs=["ndvi"]),
        NodeSpec(id="n4", type="compute_ndmi", inputs=["clean_s2"], outputs=["ndmi"]),
        NodeSpec(id="n5", type="fetch_wapor_et", params={"product": "ETa_dekadal"}, outputs=["et_raw"]),
        NodeSpec(id="n6", type="water_stress_classify",
                 params={"ndmi_thresh": 0.2, "et_deficit_thresh": 0.3},
                 inputs=["ndvi", "ndmi", "et_raw"], outputs=["stress_class"]),
    ]
    edges = [("n1", "n2"), ("n2", "n3"), ("n2", "n4"), ("n5", "n6"), ("n3", "n6"), ("n4", "n6")]
    return WorkflowGraph(workflow_id="stub", nodes=nodes, edges=edges)


def generate_graph(query: str, aoi: dict, date_range: dict) -> WorkflowGraph:
    """
    Real implementation goes here later: call the LLM with the node_catalog
    contents as context, enforce structured JSON output matching WorkflowGraph,
    then validate the result as a DAG before returning it.
    """
    return generate_graph_stub(query, aoi, date_range)