import pytest

from app.services.storage import LocalArtifactStore


def test_put_and_fetch_roundtrip(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    src = tmp_path / "src.txt"
    src.write_text("hello")

    uri = store.put(src, "run1/n1/out.txt")
    assert uri == "file://run1/n1/out.txt"
    assert store.fetch(uri).read_text() == "hello"


def test_put_json_and_read_back(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    uri = store.put_json({"a": 1, "b": [1, 2]}, "run1/n1/stats.json")
    assert store.read_json(uri) == {"a": 1, "b": [1, 2]}


def test_put_bytes_creates_nested_dirs(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    uri = store.put_bytes(b"xyz", "run1/deep/nested/n1/blob.bin")
    assert store.fetch(uri).read_bytes() == b"xyz"


def test_key_cannot_escape_root(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.put_bytes(b"x", "../escaped.txt")


def test_fetch_missing_raises(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(FileNotFoundError):
        store.fetch("file://run1/n1/nope.txt")


def test_fetch_rejects_foreign_scheme(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.fetch("s3://bucket/key")


def test_delete_prefix(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    store.put_bytes(b"a", "run1/n1/a.txt")
    store.put_bytes(b"b", "run1/n2/b.txt")
    store.put_bytes(b"c", "run2/n1/c.txt")

    assert store.delete_prefix("run1") == 2
    assert store.delete_prefix("run1") == 0
    assert store.fetch("file://run2/n1/c.txt").exists()