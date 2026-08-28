"""Node registry -- the single source of truth for the node catalog.

Flow is one direction only: NODE_REGISTRY (code) -> node_catalog table -> LLM
prompt. The previous SEED_NODES literal was a second source of truth and had
already drifted from the planner, so it is derived from here instead.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class NodeDef:
    type_name: str
    description: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    param_schema: dict[str, str]
    implementation_ref: str
    fn: Callable
    defaults: dict = field(default_factory=dict)
    hidden: bool = False        # hidden nodes are never shown to the LLM planner


NODE_REGISTRY: dict[str, NodeDef] = {}

# Every module listed here must import cleanly. Append as phases complete:
#   Phase 2: dummy
#   Phase 3: sentinel2
#   Phase 4: wapor, analysis
_NODE_MODULES = ["dummy"]

_loaded = False


def register_node(type_name: str, description: str, inputs: dict, outputs: dict,
                  params: dict, defaults: dict | None = None, hidden: bool = False):
    def deco(fn):
        NODE_REGISTRY[type_name] = NodeDef(
            type_name=type_name,
            description=description,
            input_schema=inputs,
            output_schema=outputs,
            param_schema=params,
            implementation_ref=f"{fn.__module__}.{fn.__name__}",
            fn=fn,
            defaults=defaults or {},
            hidden=hidden,
        )
        return fn

    return deco


def load_registry(include_hidden: bool = True) -> dict[str, NodeDef]:
    global _loaded
    if not _loaded:
        for mod in _NODE_MODULES:
            importlib.import_module(f"app.services.nodes.{mod}")
        _loaded = True
    if include_hidden:
        return NODE_REGISTRY
    return {k: v for k, v in NODE_REGISTRY.items() if not v.hidden}