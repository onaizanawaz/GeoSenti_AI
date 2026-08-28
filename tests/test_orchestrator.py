"""End-to-end orchestration against the real database, using dummy nodes.

Marked `db` because the state machine being tested IS the database state
machine -- mocking the session would test nothing worth testing.
"""

import pytest

from app.database import SessionLocal
from app.models import Artifact, NodeRun, Workflow, WorkflowRun
from app.services.orchestrator import run_workflow_task
from app.services.storage import get_store

pytestmark = pytest.mark.db


def chain_graph(fail: bool = False):
    return {
        "workflow_id": "t",
        "nodes": [
            {"id": "a", "type": "dummy_source", "params": {"value": 3},
             "inputs": [], "outputs": ["dummy_a"]},
            {"id": "b", "type": "dummy_transform", "params": {"fail": fail},
             "inputs": ["dummy_a"], "outputs": ["dummy_b"]},
            {"id": "c", "type": "dummy_sink", "params": {},
             "inputs": ["dummy_b"], "outputs": ["dummy_out"]},
        ],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    }


@pytest.fixture
def seeded():
    """Create a Workflow + WorkflowRun, yield the run id, then clean up."""
    created = {}

    def _make(graph):
        db = SessionLocal()
        wf = Workflow(query="test", aoi={}, date_range={}, graph=graph, status="draft")
        db.add(wf); db.commit(); db.refresh(wf)
        run = WorkflowRun(workflow_id=wf.id, status="pending", params_snapshot=graph)
        db.add(run); db.commit(); db.refresh(run)
        created["wf"], created["run"] = wf.id, run.id
        db.close()
        return str(run.id)

    yield _make

    db = SessionLocal()
    if created:
        db.query(Artifact).filter_by(workflow_run_id=created["run"]).delete()
        db.query(NodeRun).filter_by(workflow_run_id=created["run"]).delete()
        db.query(WorkflowRun).filter_by(id=created["run"]).delete()
        db.query(Workflow).filter_by(id=created["wf"]).delete()
        db.commit()
    db.close()
    get_store().delete_prefix(str(created.get("run", "nonexistent")))


def read_state(run_id):
    db = SessionLocal()
    nodes = {nr.node_id: nr.status
             for nr in db.query(NodeRun).filter_by(workflow_run_id=run_id).all()}
    run = db.query(WorkflowRun).filter_by(id=run_id).first()
    status = run.status
    db.close()
    return status, nodes


def test_happy_path_runs_in_order_and_stores_artifacts(seeded):
    run_id = seeded(chain_graph(fail=False))
    run_workflow_task(run_id)

    status, nodes = read_state(run_id)
    assert status == "done"
    assert nodes == {"a": "done", "b": "done", "c": "done"}

    db = SessionLocal()
    arts = {a.name: a for a in db.query(Artifact).filter_by(workflow_run_id=run_id).all()}
    db.close()
    assert set(arts) == {"dummy_a", "dummy_b", "dummy_out"}

    # The value threaded through the whole chain: 3 -> doubled -> written out.
    assert get_store().fetch(arts["dummy_out"].uri).read_text().strip() == "final=6"


def test_node_ordering_is_recorded(seeded):
    run_id = seeded(chain_graph(fail=False))
    run_workflow_task(run_id)

    db = SessionLocal()
    runs = {nr.node_id: nr.started_at
            for nr in db.query(NodeRun).filter_by(workflow_run_id=run_id).all()}
    db.close()
    assert runs["a"] <= runs["b"] <= runs["c"]


def test_failure_marks_descendants_skipped_not_failed(seeded):
    run_id = seeded(chain_graph(fail=True))
    run_workflow_task(run_id)

    status, nodes = read_state(run_id)
    assert status == "failed"
    assert nodes["a"] == "done"
    assert nodes["b"] == "failed"
    assert nodes["c"] == "skipped"      # NOT failed, NOT pending

    db = SessionLocal()
    nr = db.query(NodeRun).filter_by(workflow_run_id=run_id, node_id="b").first()
    assert "told to fail" in nr.error
    assert nr.logs and "Traceback" in nr.logs
    db.close()


def test_invalid_graph_fails_the_run_before_executing(seeded):
    bad = chain_graph()
    bad["nodes"][0]["type"] = "compute_vibes"
    run_id = seeded(bad)
    run_workflow_task(run_id)

    status, nodes = read_state(run_id)
    assert status == "failed"
    assert nodes == {}                  # nothing ran
    db = SessionLocal()
    assert "unknown_node_type" in db.query(WorkflowRun).filter_by(id=run_id).first().error
    db.close()


def test_missing_run_is_handled(seeded):
    import uuid
    assert run_workflow_task(str(uuid.uuid4())) == "missing"