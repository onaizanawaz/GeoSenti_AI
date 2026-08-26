"""Artifact storage.

Nodes produce named outputs; those outputs live here and are referenced from
the `artifacts` table by URI. LocalArtifactStore writes to disk today;
swapping in an S3ArtifactStore later means changing get_store() only, because
every call site goes through the ArtifactStore protocol.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings


class ArtifactStore(Protocol):
    def put(self, local_path: Path, key: str) -> str: ...
    def put_bytes(self, data: bytes, key: str) -> str: ...
    def put_json(self, obj: Any, key: str) -> str: ...
    def fetch(self, uri: str) -> Path: ...
    def read_json(self, uri: str) -> Any: ...
    def delete_prefix(self, key_prefix: str) -> int: ...


class LocalArtifactStore:
    """Filesystem-backed store. Keys are POSIX-style relative paths of the
    form '{workflow_run_id}/{node_id}/{name}.{ext}'."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        # Node output names will eventually come from an LLM; a name like
        # "../../etc/passwd" must not write outside the artifact root.
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"Key escapes artifact root: {key}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _uri(key: str) -> str:
        return f"file://{key}"

    @staticmethod
    def _key_of(uri: str) -> str:
        if not uri.startswith("file://"):
            raise ValueError(f"Not a local artifact uri: {uri}")
        return uri[len("file://"):]

    def put(self, local_path: Path, key: str) -> str:
        dest = self._path(key)
        if Path(local_path).resolve() != dest:
            shutil.copy2(local_path, dest)
        return self._uri(key)

    def put_bytes(self, data: bytes, key: str) -> str:
        self._path(key).write_bytes(data)
        return self._uri(key)

    def put_json(self, obj: Any, key: str) -> str:
        return self.put_bytes(json.dumps(obj, indent=2, default=str).encode(), key)

    def fetch(self, uri: str) -> Path:
        p = self._path(self._key_of(uri))
        if not p.exists():
            raise FileNotFoundError(uri)
        return p

    def read_json(self, uri: str) -> Any:
        return json.loads(self.fetch(uri).read_text())

    def delete_prefix(self, key_prefix: str) -> int:
        target = self._path(key_prefix)
        if not target.exists():
            return 0
        n = sum(1 for p in target.rglob("*") if p.is_file())
        shutil.rmtree(target)
        return n


_store: ArtifactStore | None = None


def get_store() -> ArtifactStore:
    """Single swap point: return S3ArtifactStore here when local disk is outgrown."""
    global _store
    if _store is None:
        _store = LocalArtifactStore(get_settings().artifact_root)
    return _store