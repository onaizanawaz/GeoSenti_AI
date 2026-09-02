"""Graph validation and topology. Pure functions: no DB, no Celery, no I/O.

Both POST /workflows/ and the Phase 6 LLM repair loop call validate_graph().
GraphError.hint exists specifically so the repair loop has something actionable
to feed back to the model.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class GraphError:
    code: str
    message: str
    node_id: str | None = None
    hint: str | None = None

    def dict(self) -> dict:
        return asdict(self)


class CycleError(Exception):
    pass


# Structural problems that should not stop a run.
WARNING_CODES = {"orphan_node", "no_terminal_node"}


def as_dict(graph) -> dict:
    """Accept a WorkflowGraph model or the raw JSONB dict."""
    if hasattr(graph, "model_dump"):
        return graph.model_dump()
    return graph


def _edge_pair(e) -> tuple[str, str]:
    """Edges may arrive as Edge models, dicts from JSONB, or legacy 2-tuples."""
    if isinstance(e, dict):
        return e["source"], e["target"]
    if isinstance(e, (list, tuple)):
        return e[0], e[1]
    return e.source, e.target


def _edge_ports(e) -> tuple[str | None, str | None]:
    """(source_output, target_input) for an edge, if it declares them."""
    if isinstance(e, dict):
        return e.get("source_output"), e.get("target_input")
    if isinstance(e, (list, tuple)):
        return None, None
    return getattr(e, "source_output", None), getattr(e, "target_input", None)


def port_inputs(graph, node_id: str) -> dict[str, tuple[str, str | None]]:
    """Explicit wiring: target_input -> (source node, source_output).

    Needed whenever a producer's output name differs from the consumer's input
    name, e.g. compute_ndvi produces "ndvi" but export_cog consumes "image".
    """
    g = as_dict(graph)
    mapping: dict[str, tuple[str, str | None]] = {}
    for e in g.get("edges") or []:
        s, t = _edge_pair(e)
        so, ti = _edge_ports(e)
        if t == node_id and ti:
            mapping[ti] = (s, so)
    return mapping


def blocking(errors: list[GraphError]) -> list[GraphError]:
    return [e for e in errors if e.code not in WARNING_CODES]


# ---------------------------------------------------------------- type checks

def _check_type(value: Any, decl: str) -> bool:
    decl = (decl or "").strip().lower()
    if decl in ("", "any"):
        return True
    if decl == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if decl == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if decl == "str":
        return isinstance(value, str)
    if decl == "bool":
        return isinstance(value, bool)
    if decl == "dict":
        return isinstance(value, dict)
    if decl.startswith("list[") and decl.endswith("]"):
        return isinstance(value, list) and all(_check_type(v, decl[5:-1]) for v in value)
    if decl.startswith("enum[") and decl.endswith("]"):
        return value in [s.strip() for s in decl[5:-1].split("|")]
    return True     # unknown declaration -> do not block


# ------------------------------------------------------------------ topology

def topo_sort(graph) -> list[str]:
    """Kahn's algorithm. Ties broken alphabetically so runs are reproducible."""
    g = as_dict(graph)
    ids = [n["id"] for n in g["nodes"]]
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = defaultdict(list)

    for e in g.get("edges") or []:
        s, t = _edge_pair(e)
        if s in indeg and t in indeg:
            adj[s].append(t)
            indeg[t] += 1

    q = deque(sorted(i for i in ids if indeg[i] == 0))
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        ready = []
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        for m in sorted(ready):
            q.append(m)

    if len(order) != len(ids):
        raise CycleError(f"Cycle involving: {sorted(set(ids) - set(order))}")
    return order


def ancestors(graph, node_id: str) -> set[str]:
    g = as_dict(graph)
    rev: dict[str, list[str]] = defaultdict(list)
    for e in g.get("edges") or []:
        s, t = _edge_pair(e)
        rev[t].append(s)
    seen: set[str] = set()
    stack = list(rev[node_id])
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(rev[n])
    return seen


def descendants(graph, node_id: str) -> set[str]:
    g = as_dict(graph)
    fwd: dict[str, list[str]] = defaultdict(list)
    for e in g.get("edges") or []:
        s, t = _edge_pair(e)
        fwd[s].append(t)
    seen: set[str] = set()
    stack = list(fwd[node_id])
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(fwd[n])
    return seen


def predecessors(graph, node_id: str) -> list[str]:
    """Direct upstream nodes only -- ancestors() is the transitive closure."""
    g = as_dict(graph)
    return sorted({s for e in (g.get("edges") or [])
                   for s, t in [_edge_pair(e)] if t == node_id})


def waves(graph) -> list[list[str]]:
    """Nodes grouped by longest-path depth: everything in wave n can run
    concurrently once wave n-1 is done.

    Longest path, not shortest: a node must wait for its slowest ancestor, so
    depth = 1 + max(depth of predecessors). Shortest path would schedule a node
    into a wave before one of its inputs exists.
    """
    g = as_dict(graph)
    depth: dict[str, int] = {}
    for node_id in topo_sort(g):            # raises CycleError, as callers expect
        preds = predecessors(g, node_id)
        depth[node_id] = 1 + max((depth[p] for p in preds), default=-1)

    out: list[list[str]] = [[] for _ in range(max(depth.values(), default=-1) + 1)]
    for node_id, d in depth.items():
        out[d].append(node_id)
    return [sorted(w) for w in out]


# A node is "settled" when nothing further will happen to it. Anything else is
# either dispatchable or still in flight.
DONE_STATES = frozenset({"done"})
FAILED_STATES = frozenset({"failed", "skipped", "cancelled"})
INFLIGHT_STATES = frozenset({"queued", "running"})


def next_actions(graph, statuses: dict[str, str]) -> tuple[list[str], list[str]]:
    """(dispatchable, to_skip) given the current per-node statuses.

    Pure, so the scheduler's decisions are testable without a database or a
    broker. A node is dispatchable once every direct predecessor is done; it is
    skipped as soon as any ancestor has failed, without waiting for the rest of
    its wave. Unknown or 'pending' both mean "not started".
    """
    g = as_dict(graph)
    ids = [n["id"] for n in g.get("nodes") or []]

    def state(n: str) -> str:
        return statuses.get(n) or "pending"

    ready: list[str] = []
    skip: list[str] = []

    for node_id in ids:
        if state(node_id) != "pending":
            continue
        if any(state(a) in FAILED_STATES for a in ancestors(g, node_id)):
            skip.append(node_id)
        elif all(state(p) in DONE_STATES for p in predecessors(g, node_id)):
            ready.append(node_id)

    return sorted(ready), sorted(skip)


def in_flight(statuses: dict[str, str]) -> list[str]:
    return sorted(n for n, s in statuses.items() if s in INFLIGHT_STATES)


def output_producers(graph) -> dict[str, list[str]]:
    """output name -> [node ids producing it]"""
    g = as_dict(graph)
    out: dict[str, list[str]] = defaultdict(list)
    for n in g["nodes"]:
        for o in n.get("outputs") or []:
            out[o].append(n["id"])
    return out


def resolve_inputs(graph, node_id: str) -> dict[str, tuple[str, str]]:
    """input name -> (producer node id, output name). Assumes graph validated."""
    g = as_dict(graph)
    node = next(n for n in g["nodes"] if n["id"] == node_id)
    prod = output_producers(g)
    anc = ancestors(g, node_id)

    resolved: dict[str, tuple[str, str]] = {}

    # Explicit port wiring wins, and is the only way to connect an output whose
    # name differs from the input it feeds.
    node_lookup = {n["id"]: n for n in g["nodes"]}
    for target_input, (src, src_out) in port_inputs(g, node_id).items():
        if src_out is None:
            outs = (node_lookup.get(src) or {}).get("outputs") or []
            src_out = outs[0] if len(outs) == 1 else None
        if src_out is not None:
            resolved[target_input] = (src, src_out)

    for inp in node.get("inputs") or []:
        if "." in inp:                       # qualified form "n2.clean_s2"
            pid, oname = inp.split(".", 1)
            resolved[oname] = (pid, oname)
            continue
        candidates = [p for p in prod.get(inp, []) if p in anc]
        if len(candidates) == 1:
            resolved[inp] = (candidates[0], inp)
    return resolved


# ---------------------------------------------------------------- validation

def validate_graph(graph, registry: dict) -> list[GraphError]:
    errs: list[GraphError] = []
    g = as_dict(graph)
    nodes = g.get("nodes") or []
    edges = g.get("edges") or []

    if not nodes:
        return [GraphError("empty_graph", "Graph has no nodes.",
                           hint="Emit at least one node.")]

    ids = [n["id"] for n in nodes]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(GraphError("duplicate_node_id",
                               f"Node id '{dup}' is used more than once.", dup,
                               "Give every node a unique id."))
    idset = set(ids)

    for e in edges:
        s, t = _edge_pair(e)
        if s not in idset:
            errs.append(GraphError("dangling_edge",
                                   f"Edge source '{s}' is not a node in this graph.", s))
        if t not in idset:
            errs.append(GraphError("dangling_edge",
                                   f"Edge target '{t}' is not a node in this graph.", t))

    visible = sorted(k for k, v in registry.items() if not v.hidden)

    for n in nodes:
        nd = registry.get(n["type"])
        if nd is None:
            hint = (f"Use one of: {', '.join(visible)}" if visible else
                    "The catalog is empty -- no node implementations are registered.")
            errs.append(GraphError(
                "unknown_node_type",
                f"Node type '{n['type']}' is not in the catalog.", n["id"], hint))
            continue

        for k, v in (n.get("params") or {}).items():
            if k not in nd.param_schema:
                errs.append(GraphError(
                    "unknown_param", f"'{k}' is not a parameter of '{n['type']}'.",
                    n["id"], f"Valid params: {sorted(nd.param_schema)}"))
            elif not _check_type(v, nd.param_schema[k]):
                errs.append(GraphError(
                    "param_type_mismatch",
                    f"Param '{k}' must be {nd.param_schema[k]}, got "
                    f"{type(v).__name__} ({v!r}).", n["id"]))

        declared = set(n.get("outputs") or [])
        expected = set(nd.output_schema)
        if declared and declared != expected:
            errs.append(GraphError(
                "output_mismatch",
                f"Node '{n['id']}' declares outputs {sorted(declared)}, but "
                f"'{n['type']}' produces {sorted(expected)}.", n["id"],
                f"Set outputs to {sorted(expected)}."))

    # Input resolution relies on output names being unique across the graph.
    prod = output_producers(g)
    for name, producers in sorted(prod.items()):
        if len(producers) > 1:
            errs.append(GraphError(
                "output_name_collision",
                f"Output name '{name}' is produced by {sorted(producers)}.",
                sorted(producers)[0],
                "Rename one, or reference it as '<node_id>.<output>'."))

    try:
        topo_sort(g)
    except CycleError as e:
        errs.append(GraphError("cycle", str(e),
                               hint="Remove the edge that closes the loop."))
        return errs     # every check below assumes acyclicity

    for n in nodes:
        nd = registry.get(n["type"])
        if nd is None:
            continue
        anc = ancestors(g, n["id"])
        declared_inputs = n.get("inputs") or []

        for inp in declared_inputs:
            if "." in inp:
                pid, _ = inp.split(".", 1)
                if pid not in idset:
                    errs.append(GraphError("unsatisfied_input",
                                           f"Input '{inp}' names unknown node '{pid}'.",
                                           n["id"]))
                elif pid not in anc:
                    errs.append(GraphError(
                        "unsatisfied_input",
                        f"Input '{inp}' comes from '{pid}', which is not upstream.",
                        n["id"], f"Add an edge {pid} -> {n['id']}."))
                continue

            candidates = [p for p in prod.get(inp, []) if p in anc]
            if not candidates:
                anywhere = sorted(prod.get(inp, []))
                hint = (f"'{inp}' is produced by {anywhere} -- add an edge into "
                        f"'{n['id']}'." if anywhere else
                        f"No node produces '{inp}'. Add a node whose outputs include it.")
                errs.append(GraphError(
                    "unsatisfied_input",
                    f"Node '{n['id']}' needs input '{inp}' but nothing upstream "
                    f"provides it.", n["id"], hint))

        bare = {i.split(".")[-1] for i in declared_inputs}
        bare |= set(port_inputs(g, n["id"]))
        for req in nd.input_schema:
            if req not in bare:
                errs.append(GraphError(
                    "missing_required_input",
                    f"'{n['type']}' requires input '{req}'.", n["id"],
                    f"Add '{req}' to inputs and wire the node that produces it."))

    # Warnings -- reported but non-blocking.
    sources = {s for s, _ in (_edge_pair(e) for e in edges)}
    targets = {t for _, t in (_edge_pair(e) for e in edges)}
    if len(nodes) > 1:
        for n in nodes:
            if n["id"] not in sources and n["id"] not in targets:
                errs.append(GraphError("orphan_node",
                                       f"Node '{n['id']}' is not connected to anything.",
                                       n["id"]))
    if idset and not (idset - sources):
        errs.append(GraphError("no_terminal_node",
                               "Every node feeds another; there is no final result node."))

    return errs