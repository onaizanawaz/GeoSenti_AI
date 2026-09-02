"""Prompt construction for the graph planner.

The catalog is generated from NODE_REGISTRY, never hand-written here. A
hand-maintained node list in a prompt is a third source of truth and would
drift exactly the way SEED_NODES did -- see node_catalog.py.

Hidden nodes are excluded: the dummy test nodes must never be reachable by a
user query.
"""

from __future__ import annotations

import json

from app.services.node_catalog import catalog_entries

SYSTEM = """You are a geospatial workflow planner. You translate a user's \
question about their field into a directed acyclic graph of processing nodes.

You MUST reply with a single JSON object and nothing else. No prose, no \
markdown fences, no explanation.

The JSON object has exactly two keys:

{
  "nodes": [
    {"id": "n1", "type": "<node type>", "params": {}, "inputs": [], "outputs": []}
  ],
  "edges": [
    {"source": "n1", "target": "n2"}
  ]
}

Rules, all of which are checked before your graph is accepted:

1. "type" MUST be one of the catalog types below. Never invent a node type.
2. "outputs" MUST be exactly the output names the catalog lists for that type.
3. "inputs" MUST be output names produced by an ancestor node.
4. Every output name must be unique across the whole graph.
5. "params" keys must exist in the catalog's param_schema for that type, and \
values must match the declared type. Omit a param to accept its default.
6. The graph must be acyclic, and every non-source node must be reachable \
from a source node.
7. Use the smallest graph that answers the question. Do not add nodes the \
question does not need.
8. Node ids are short and sequential: n1, n2, n3...

The AOI and date range are supplied separately by the system and are available \
to every node. Never put coordinates or dates in params.

CATALOG:
"""

USER = """User question: {query}

Area of interest: {aoi_summary}
Date range: {start} to {end}

Return the JSON graph."""

REPAIR = """Your previous graph was rejected by the validator.

Graph you produced:
{graph}

Validation errors:
{errors}

Fix every error and return the corrected JSON graph. Return only the JSON \
object, with no other text."""


def catalog_block(entries: list[dict] | None = None) -> str:
    """The catalog as compact JSON. Compact on purpose: a prompt padded with
    whitespace costs tokens on every repair round trip too."""
    entries = catalog_entries() if entries is None else entries
    trimmed = [
        {
            "type": e["type_name"],
            "description": e["description"],
            "inputs": e["input_schema"],
            "outputs": e["output_schema"],
            "params": e["param_schema"],
        }
        for e in entries
    ]
    return json.dumps(trimmed, indent=1)


def build_system(entries: list[dict] | None = None) -> str:
    return SYSTEM + catalog_block(entries)


def _aoi_summary(aoi: dict) -> str:
    """Geometry type and bounds only. The full coordinate list can be thousands
    of tokens and the planner cannot use it -- nodes receive the real AOI."""
    if not aoi:
        return "not specified"
    kind = aoi.get("type", "unknown")
    try:
        from app.services.raster import geom_of
        minx, miny, maxx, maxy = geom_of(aoi).bounds
        return (f"{kind}, bounds "
                f"[{minx:.4f}, {miny:.4f}, {maxx:.4f}, {maxy:.4f}]")
    except Exception:                       # noqa: BLE001 - a bad AOI is the router's problem
        return kind


def build_user(query: str, aoi: dict, date_range: dict) -> str:
    return USER.format(
        query=query,
        aoi_summary=_aoi_summary(aoi or {}),
        start=(date_range or {}).get("start", "not specified"),
        end=(date_range or {}).get("end", "not specified"),
    )


def build_repair(graph_json: str, errors) -> str:
    lines = "\n".join(
        f"- [{e.code}] {e.message}"
        + (f" (node {e.node_id})" if getattr(e, "node_id", None) else "")
        + (f" Hint: {e.hint}" if getattr(e, "hint", None) else "")
        for e in errors
    )
    return REPAIR.format(graph=graph_json, errors=lines)