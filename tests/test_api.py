"""API-level end-to-end: create -> run -> status -> artifacts, with Celery
running tasks inline."""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Artifact, NodeRun, Workflow, WorkflowRun
from app.services.storage import get_store

pytestmark = pytest.mark.db

client = TestClient(app)

PAYLOAD = {
    "query": "water stress in my field",
    "aoi": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    "date_range": {"start": "2024-06-01", "end": "2024-09-30"},
}


@pytest.fixture(autouse=True)
def authenticated(org):
    """Every route in this suite now requires a bearer token.

    Injected into the shared client rather than passed per call, so the tests
    keep reading as API-shape tests; auth itself is covered in test_auth.py.
    """
    client.headers.update(org["headers"])
    yield org
    client.headers.pop("Authorization", None)


@pytest.fixture(autouse=True)
def dummy_graph(monkeypatch):
    """These tests prove the API/orchestrator plumbing, not the science.

    Phase 4 pointed generate_graph() at the real water-stress graph, which
    needs live Earth Engine credentials and real imagery over the AOI. Pinning
    the router to the dummy chain keeps this suite offline and deterministic;
    the real graph is covered by test_analysis.py and the gee-marked tests.
    """
    from app.routers import workflows
    from app.services.planner import generate_graph_dummy
    monkeypatch.setattr(workflows, "generate_graph", generate_graph_dummy)


@pytest.fixture
def cleanup():
    ids = {}
    yield ids
    db = SessionLocal()
    if "workflow" in ids:
        run_ids = [r.id for r in
                   db.query(WorkflowRun).filter_by(workflow_id=ids["workflow"]).all()]
        # Children first, committing between levels so the FKs are satisfied
        # at every step.
        for rid in run_ids:
            db.query(Artifact).filter_by(workflow_run_id=rid).delete()
            db.query(NodeRun).filter_by(workflow_run_id=rid).delete()
            get_store().delete_prefix(str(rid))
        db.commit()
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids or [None])).delete(
            synchronize_session=False)
        db.commit()
        db.query(Workflow).filter_by(id=ids["workflow"]).delete()
        db.commit()
    db.close()


def test_create_persists_the_real_workflow_id_not_stub(cleanup):
    r = client.post("/workflows/", json=PAYLOAD)
    assert r.status_code == 200
    wf_id = r.json()["workflow_id"]
    cleanup["workflow"] = wf_id
    assert wf_id != "stub"

    stored = client.get(f"/workflows/{wf_id}").json()
    # The persisted graph must carry the real id, not "stub".
    assert stored["graph"]["workflow_id"] == wf_id


def test_full_run_produces_artifacts(celery_eager, cleanup):
    wf_id = client.post("/workflows/", json=PAYLOAD).json()["workflow_id"]
    cleanup["workflow"] = wf_id

    run = client.post(f"/workflows/{wf_id}/run")
    assert run.status_code == 200
    run_id = run.json()["id"]

    status = client.get(f"/workflows/{wf_id}/status").json()
    assert status["status"] == "done"
    assert {n["status"] for n in status["nodes"]} == {"done"}
    assert len(status["nodes"]) == 3

    arts = client.get(f"/runs/{run_id}/artifacts").json()
    assert {a["name"] for a in arts} == {"dummy_a", "dummy_b", "dummy_out"}

    out = next(a for a in arts if a["name"] == "dummy_out")
    dl = client.get(f"/artifacts/{out['id']}/download")
    assert dl.status_code == 200
    assert "final=6" in dl.text


def test_rerun_creates_a_second_run(celery_eager, cleanup):
    wf_id = client.post("/workflows/", json=PAYLOAD).json()["workflow_id"]
    cleanup["workflow"] = wf_id

    first = client.post(f"/workflows/{wf_id}/run").json()["id"]
    second = client.post(f"/workflows/{wf_id}/run").json()["id"]
    assert first != second

    runs = client.get(f"/workflows/{wf_id}/runs").json()
    assert len(runs) == 2
    # /status reports the latest run only, not both interleaved.
    assert client.get(f"/workflows/{wf_id}/status").json()["id"] == second


def test_status_404_before_any_run(cleanup):
    wf_id = client.post("/workflows/", json=PAYLOAD).json()["workflow_id"]
    cleanup["workflow"] = wf_id
    assert client.get(f"/workflows/{wf_id}/status").status_code == 404


def test_catalog_hides_dummy_nodes():
    entries = client.get("/catalog").json()
    assert all(not e["type_name"].startswith("dummy_") for e in entries)