"""The contract every node implementation follows.

A node is a plain function taking (NodeContext, dict[str, ArtifactRef]) and
returning a list of Produced. It never touches the database, never decides its
own status, and never writes to the artifact store directly -- the orchestrator
does all of that, in one place, for every node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from logging import Logger
from pathlib import Path
from typing import Any


class NodeError(Exception):
    """Base for node failures that carry a user-facing message."""
    code = "node_error"


class NodeInputError(NodeError):
    code = "node_input_error"


class NoImageryError(NodeError):
    code = "no_imagery"


class AoiTooLargeError(NodeError):
    code = "aoi_too_large"

    def __init__(self, message: str, suggested_scale: int | None = None):
        super().__init__(message)
        self.suggested_scale = suggested_scale


@dataclass
class ArtifactRef:
    """A resolved upstream output, handed to a node as one of its inputs."""
    id: str
    node_id: str
    name: str
    kind: str
    uri: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeContext:
    workflow_run_id: str
    node_id: str
    node_type: str
    params: dict[str, Any]          # registry defaults merged with graph params
    aoi: dict[str, Any]
    date_range: dict[str, str]
    store: Any                      # ArtifactStore
    db: Any                         # sqlalchemy Session
    log: Logger
    workdir: Path                   # scratch dir, removed after the node finishes


@dataclass
class Produced:
    """One output of a node. Exactly one of local_path / value must be set."""
    name: str
    kind: str                       # raster|vector|table|json|scalar|ee_object
    local_path: Path | None = None
    value: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    ext: str | None = None          # override the extension inferred from kind