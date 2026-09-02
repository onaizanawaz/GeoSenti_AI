import pytest

from app.services.dag import (CycleError, ancestors, blocking, descendants,
                              in_flight, next_actions, predecessors,
                              resolve_inputs, topo_sort, validate_graph, waves)
from app.services.nodes import load_registry

REG = load_registry()


def g(nodes, edges):
    return {"workflow_id": "t", "nodes": nodes,
            "edges": [{"source": s, "target": t} for s, t in edges]}


def chain():
    """a -> b -> c using the dummy nodes."""
    return g(
        [
            {"id": "a", "type": "dummy_source", "params": {"value": 2},
             "inputs": [], "outputs": ["dummy_a"]},
            {"id": "b", "type": "dummy_transform", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_b"]},
            {"id": "c", "type": "dummy_sink", "params": {},
             "inputs": ["dummy_b"], "outputs": ["dummy_out"]},
        ],
        [("a", "b"), ("b", "c")],
    )


def codes(errs):
    return {e.code for e in errs}


def test_valid_graph_has_no_errors():
    assert validate_graph(chain(), REG) == []


def test_topo_order_respects_edges():
    order = topo_sort(chain())
    assert order.index("a") < order.index("b") < order.index("c")


def test_cycle_detected():
    graph = chain()
    graph["edges"].append({"source": "c", "target": "a"})
    assert "cycle" in codes(validate_graph(graph, REG))
    with pytest.raises(CycleError):
        topo_sort(graph)


def test_unknown_node_type():
    graph = chain()
    graph["nodes"][0]["type"] = "compute_vibes"
    assert "unknown_node_type" in codes(validate_graph(graph, REG))


def test_unknown_node_type_hint_lists_only_visible_nodes():
    graph = chain()
    graph["nodes"][0]["type"] = "compute_vibes"
    err = next(e for e in validate_graph(graph, REG) if e.code == "unknown_node_type")
    # dummy_* are hidden=True, so they must not be advertised.
    assert "dummy_source" not in (err.hint or "")


def test_duplicate_node_id():
    graph = chain()
    graph["nodes"][1]["id"] = "a"
    assert "duplicate_node_id" in codes(validate_graph(graph, REG))


def test_unsatisfied_input_when_edge_removed():
    graph = chain()
    graph["edges"] = [{"source": "b", "target": "c"}]     # a no longer feeds b
    errs = validate_graph(graph, REG)
    assert "unsatisfied_input" in codes(errs)
    err = next(e for e in errs if e.code == "unsatisfied_input")
    assert "a" in (err.hint or "")      # hint should name the real producer


def test_missing_required_input():
    graph = chain()
    graph["nodes"][1]["inputs"] = []
    assert "missing_required_input" in codes(validate_graph(graph, REG))


def test_output_name_collision():
    graph = chain()
    graph["nodes"].append({"id": "d", "type": "dummy_source", "params": {},
                           "inputs": [], "outputs": ["dummy_a"]})
    graph["edges"].append({"source": "d", "target": "b"})
    assert "output_name_collision" in codes(validate_graph(graph, REG))


def test_output_mismatch():
    graph = chain()
    graph["nodes"][0]["outputs"] = ["wrong_name"]
    assert "output_mismatch" in codes(validate_graph(graph, REG))


def test_param_type_mismatch():
    graph = chain()
    graph["nodes"][0]["params"] = {"value": "not-an-int"}
    assert "param_type_mismatch" in codes(validate_graph(graph, REG))


def test_bool_is_not_an_int():
    graph = chain()
    graph["nodes"][0]["params"] = {"value": True}
    assert "param_type_mismatch" in codes(validate_graph(graph, REG))


def test_unknown_param():
    graph = chain()
    graph["nodes"][0]["params"] = {"nonsense": 1}
    assert "unknown_param" in codes(validate_graph(graph, REG))


def test_dangling_edge():
    graph = chain()
    graph["edges"].append({"source": "a", "target": "zzz"})
    assert "dangling_edge" in codes(validate_graph(graph, REG))


def test_empty_graph():
    assert "empty_graph" in codes(validate_graph({"nodes": [], "edges": []}, REG))


def test_orphan_node_is_a_warning_not_blocking():
    graph = chain()
    # outputs left empty: declaring none is allowed, and it avoids colliding
    # with node 'a', which already produces dummy_a.
    graph["nodes"].append({"id": "z", "type": "dummy_source", "params": {},
                           "inputs": [], "outputs": []})
    errs = validate_graph(graph, REG)
    assert "orphan_node" in codes(errs)
    assert blocking(errs) == []


def test_legacy_tuple_edges_still_parse():
    """Graphs stored before Edge became a model round-trip as lists."""
    graph = chain()
    graph["edges"] = [["a", "b"], ["b", "c"]]
    assert validate_graph(graph, REG) == []
    assert topo_sort(graph) == ["a", "b", "c"]


def test_resolve_inputs_and_relatives():
    graph = chain()
    assert resolve_inputs(graph, "c") == {"dummy_b": ("b", "dummy_b")}
    assert ancestors(graph, "c") == {"a", "b"}
    assert descendants(graph, "a") == {"b", "c"}


# --- real Sentinel-2 chain (Phase 3) --------------------------------------

def s2_chain():
    """fetch -> mask -> ndvi -> export_cog.

    export_cog consumes "image" while compute_ndvi produces "ndvi", so the last
    edge carries an explicit port mapping.
    """
    return {
        "workflow_id": "t",
        "nodes": [
            {"id": "n1", "type": "fetch_sentinel2", "params": {"max_cloud_pct": 60},
             "inputs": [], "outputs": ["raw_s2"]},
            {"id": "n2", "type": "cloud_mask", "params": {"cloud_prob_thresh": 40},
             "inputs": ["raw_s2"], "outputs": ["clean_s2"]},
            {"id": "n3", "type": "compute_ndvi", "params": {"reducer": "median"},
             "inputs": ["clean_s2"], "outputs": ["ndvi"]},
            {"id": "n4", "type": "export_cog", "params": {"scale": 10},
             "inputs": [], "outputs": ["cog"]},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4",
             "source_output": "ndvi", "target_input": "image"},
        ],
    }


def test_sentinel2_chain_validates():
    assert validate_graph(s2_chain(), REG) == []


def test_sentinel2_chain_topo_order():
    assert topo_sort(s2_chain()) == ["n1", "n2", "n3", "n4"]


def test_port_mapping_resolves_ndvi_to_image():
    assert resolve_inputs(s2_chain(), "n4") == {"image": ("n3", "ndvi")}


def test_missing_port_leaves_required_input_unsatisfied():
    graph = s2_chain()
    graph["edges"][-1].pop("target_input")
    assert "missing_required_input" in codes(validate_graph(graph, REG))


def test_bad_reducer_rejected_by_enum():
    graph = s2_chain()
    graph["nodes"][2]["params"]["reducer"] = "mode"
    assert "param_type_mismatch" in codes(validate_graph(graph, REG))


# ---------------------------------------------------------------- scheduling

def diamond():
    """a -> {b, c}: one source, two independent consumers."""
    return g(
        [
            {"id": "a", "type": "dummy_source", "params": {},
             "inputs": [], "outputs": ["dummy_a"]},
            {"id": "b", "type": "dummy_transform", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_b"]},
            {"id": "c", "type": "dummy_branch", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_c"]},
        ],
        [("a", "b"), ("a", "c")],
    )


def test_predecessors_are_direct_only():
    assert predecessors(chain(), "c") == ["b"]      # not ["a", "b"]
    assert predecessors(chain(), "a") == []


def test_waves_group_independent_nodes_together():
    assert waves(diamond()) == [["a"], ["b", "c"]]


def test_a_chain_is_one_node_per_wave():
    assert waves(chain()) == [["a"], ["b"], ["c"]]


def test_wave_depth_follows_the_longest_path_not_the_shortest():
    # d depends on both a (direct) and c (three hops). Scheduling it in wave 1
    # beside b would run it before c has produced anything.
    graph = g(
        [
            {"id": "a", "type": "dummy_source", "params": {},
             "inputs": [], "outputs": ["dummy_a"]},
            {"id": "b", "type": "dummy_transform", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_b"]},
            {"id": "c", "type": "dummy_sink", "params": {},
             "inputs": ["dummy_b"], "outputs": ["dummy_out"]},
            {"id": "d", "type": "dummy_branch", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_c"]},
        ],
        [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")],
    )
    assert waves(graph) == [["a"], ["b"], ["c"], ["d"]]


def test_nothing_but_the_roots_is_ready_at_the_start():
    ready, skip = next_actions(diamond(), {})
    assert ready == ["a"] and skip == []


def test_both_branches_become_ready_together():
    ready, skip = next_actions(diamond(), {"a": "done"})
    assert ready == ["b", "c"] and skip == []


def test_nothing_is_dispatched_while_a_predecessor_is_running():
    ready, _ = next_actions(diamond(), {"a": "running"})
    assert ready == []


def test_a_queued_node_is_not_dispatched_again():
    # This is what stops a second tick from double-queueing the same node.
    ready, _ = next_actions(diamond(), {"a": "done", "b": "queued"})
    assert ready == ["c"]


def test_failure_skips_descendants_but_not_siblings():
    ready, skip = next_actions(diamond(), {"a": "done", "b": "failed"})
    assert ready == ["c"]      # c is unaffected by b
    assert skip == []


def test_a_failed_root_skips_everything_downstream():
    ready, skip = next_actions(diamond(), {"a": "failed"})
    assert ready == [] and skip == ["b", "c"]


def test_skipping_propagates_transitively():
    _, skip = next_actions(chain(), {"a": "skipped"})
    assert skip == ["b", "c"]


def test_in_flight_reports_queued_and_running_only():
    assert in_flight({"a": "done", "b": "queued", "c": "running",
                      "d": "failed", "e": "skipped"}) == ["b", "c"]


def test_no_ready_and_none_in_flight_is_the_terminal_state():
    statuses = {"a": "done", "b": "done", "c": "done"}
    ready, skip = next_actions(diamond(), statuses)
    assert (ready, skip, in_flight(statuses)) == ([], [], [])