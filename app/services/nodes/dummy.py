"""Test-only nodes. hidden=True keeps them out of the catalog the LLM sees.

The whole orchestration test suite runs on these, so ordering, artifact
passing and failure propagation can be proven without touching Earth Engine.
"""

from app.services.nodes import register_node
from app.services.nodes.base import NodeContext, Produced


@register_node(
    "dummy_source",
    "Test node: emits a small JSON payload.",
    inputs={}, outputs={"dummy_a": "json"},
    params={"value": "int"}, defaults={"value": 1}, hidden=True,
)
def dummy_source(ctx: NodeContext, inputs):
    return [Produced(name="dummy_a", kind="json",
                     value={"value": ctx.params["value"]},
                     meta={"node": ctx.node_id})]


@register_node(
    "dummy_transform",
    "Test node: doubles the incoming value. Raises if params.fail is true.",
    inputs={"dummy_a": "json"}, outputs={"dummy_b": "json"},
    params={"fail": "bool"}, defaults={"fail": False}, hidden=True,
)
def dummy_transform(ctx: NodeContext, inputs):
    if ctx.params["fail"]:
        raise RuntimeError("dummy_transform was told to fail")
    src = ctx.store.read_json(inputs["dummy_a"].uri)
    return [Produced(name="dummy_b", kind="json", value={"value": src["value"] * 2})]


@register_node(
    "dummy_branch",
    "Test node: a second, independent consumer of dummy_a. Exists so a graph "
    "can fan out and a scheduling wave can be more than one node wide.",
    inputs={"dummy_a": "json"}, outputs={"dummy_c": "json"},
    params={}, hidden=True,
)
def dummy_branch(ctx: NodeContext, inputs):
    src = ctx.store.read_json(inputs["dummy_a"].uri)
    return [Produced(name="dummy_c", kind="json", value={"value": src["value"] + 1})]


@register_node(
    "dummy_sink",
    "Test node: writes a text file from the incoming value.",
    inputs={"dummy_b": "json"}, outputs={"dummy_out": "table"},
    params={}, hidden=True,
)
def dummy_sink(ctx: NodeContext, inputs):
    src = ctx.store.read_json(inputs["dummy_b"].uri)
    p = ctx.workdir / "result.txt"
    p.write_text(f"final={src['value']}\n")
    return [Produced(name="dummy_out", kind="table", local_path=p, ext=".txt")]