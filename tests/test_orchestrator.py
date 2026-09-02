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
def seeded(org):
    """Create a Workflow + WorkflowRun, yield the run id, then clean up.

    Workflows are org-scoped since Phase 7, so this borrows an org rather than
    inventing tenancy of its own.
    """
    created = []

    def _make(graph):
        db = SessionLocal()
        wf = Workflow(query="test", aoi={}, date_range={}, graph=graph,
                      status="draft", org_id=org["org_id"])
        db.add(wf); db.commit(); db.refresh(wf)
        run = WorkflowRun(workflow_id=wf.id, status="pending", params_snapshot=graph)
        db.add(run); db.commit(); db.refresh(run)
        # A list, not a dict: a test may seed more than one run, and keeping
        # only the last one leaked the others' rows into the next test.
        created.append((wf.id, run.id))
        db.close()
        return str(run.id)

    yield _make

    db = SessionLocal()
    for wf_id, run_id in created:
        db.query(Artifact).filter_by(workflow_run_id=run_id).delete()
        db.query(NodeRun).filter_by(workflow_run_id=run_id).delete()
        db.query(WorkflowRun).filter_by(id=run_id).delete()
        db.query(Workflow).filter_by(id=wf_id).delete()
        db.commit()
    db.close()
    for _, run_id in created:
        get_store().delete_prefix(str(run_id))


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


# ------------------------------------------------------- parallel strategy

def diamond_graph(fail: bool = False):
    """a -> {b, c}. b and c are independent, so they share one wave."""
    return {
        "workflow_id": "t",
        "nodes": [
            {"id": "a", "type": "dummy_source", "params": {"value": 3},
             "inputs": [], "outputs": ["dummy_a"]},
            {"id": "b", "type": "dummy_transform", "params": {"fail": fail},
             "inputs": ["dummy_a"], "outputs": ["dummy_b"]},
            {"id": "c", "type": "dummy_branch", "params": {},
             "inputs": ["dummy_a"], "outputs": ["dummy_c"]},
        ],
        "edges": [{"source": "a", "target": "b"}, {"source": "a", "target": "c"}],
    }


@pytest.fixture
def parallel():
    """Switch the orchestrator to wave dispatch for one test.

    get_settings() is lru_cached, so the instance is shared -- set the field
    and put it back rather than clearing the cache out from under other tests.
    """
    from app.config import get_settings
    s = get_settings()
    before = s.execution_strategy
    s.execution_strategy = "parallel"
    yield s
    s.execution_strategy = before


def test_parallel_strategy_completes_a_chain(parallel, celery_eager, seeded):
    run_id = seeded(chain_graph(fail=False))
    run_workflow_task(run_id)

    status, nodes = read_state(run_id)
    assert status == "done"
    assert nodes == {"a": "done", "b": "done", "c": "done"}
    assert get_store().fetch(
        SessionLocal().query(Artifact)
        .filter_by(workflow_run_id=run_id, name="dummy_out").first().uri
    ).read_text().strip() == "final=6"


def test_parallel_strategy_runs_a_two_wide_wave(parallel, celery_eager, seeded):
    run_id = seeded(diamond_graph())

    # "running", not "done": the first tick dispatches and reschedules, and the
    # run is finalized by a later tick. A final status here would mean the
    # strategy switch silently fell through to the sequential walk.
    assert run_workflow_task(run_id) == "running"

    status, nodes = read_state(run_id)
    assert status == "done"
    assert nodes == {"a": "done", "b": "done", "c": "done"}

    db = SessionLocal()
    arts = {a.name for a in db.query(Artifact).filter_by(workflow_run_id=run_id).all()}
    db.close()
    assert arts == {"dummy_a", "dummy_b", "dummy_c"}


def test_parallel_failure_skips_descendants_but_not_siblings(parallel, celery_eager,
                                                             seeded):
    run_id = seeded(diamond_graph(fail=True))
    run_workflow_task(run_id)

    status, nodes = read_state(run_id)
    assert status == "failed"
    assert nodes["b"] == "failed"
    # c does not depend on b, so a sibling failure must not take it down.
    assert nodes["c"] == "done"


def test_parallel_leaves_no_node_stuck_in_flight(parallel, celery_eager, seeded):
    run_id = seeded(diamond_graph())
    run_workflow_task(run_id)

    _, nodes = read_state(run_id)
    assert not {"queued", "running", "pending"} & set(nodes.values())


def test_parallel_rejects_an_invalid_graph_without_creating_rows(parallel,
                                                                 celery_eager, seeded):
    bad = diamond_graph()
    bad["nodes"][0]["type"] = "compute_vibes"
    run_id = seeded(bad)
    assert run_workflow_task(run_id) == "invalid"

    status, nodes = read_state(run_id)
    assert status == "failed"
    assert nodes == {}


def test_both_strategies_agree_on_the_final_state(celery_eager, seeded):
    """The point of sharing execute_one(): switching strategy must not change
    what a run means."""
    from app.config import get_settings
    s = get_settings()
    before = s.execution_strategy

    s.execution_strategy = "sequential"
    seq = seeded(chain_graph())
    run_workflow_task(seq)
    seq_state = read_state(seq)

    s.execution_strategy = "parallel"
    try:
        par = seeded(chain_graph())
        run_workflow_task(par)
        par_state = read_state(par)
    finally:
        s.execution_strategy = before

    assert seq_state == par_state