import pytest

from app.services.dag import (CycleError, ancestors, blocking, descendants,
                              resolve_inputs, topo_sort, validate_graph)
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