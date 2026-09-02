import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import NodeRun, User, Workflow, WorkflowRun
from app.schemas import (NodeRunStatus, WorkflowCreate, WorkflowGraph,
                         WorkflowRunOut)
from app.services.auth import current_user
from app.services.dag import blocking, validate_graph
from app.services.nodes import load_registry
from app.services.orchestrator import run_workflow_task
from app.services.planner import generate_graph

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _validate_or_422(graph):
    errors = blocking(validate_graph(graph, load_registry()))
    if errors:
        raise HTTPException(status_code=422, detail=[e.dict() for e in errors])


def _as_uuid(value: str):
    """Postgres raises on a malformed uuid, which surfaced as a 500. A bad id
    is a client error, and an unfindable one at that, so it is a 404."""
    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "Workflow not found")


def _owned_workflow(db: Session, workflow_id: str, user: User) -> Workflow:
    """The single tenancy chokepoint for this router.

    Another org's workflow is reported as 404, not 403: a 403 confirms the id
    exists, which leaks one org's data to another one id at a time.
    """
    wf = (db.query(Workflow)
          .filter_by(id=_as_uuid(workflow_id), org_id=user.org_id)
          .first())
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.post("/", response_model=WorkflowGraph)
def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    graph = generate_graph(payload.query, payload.aoi, payload.date_range)
    _validate_or_422(graph)

    workflow = Workflow(query=payload.query, aoi=payload.aoi,
                        date_range=payload.date_range, graph={}, status="draft",
                        org_id=user.org_id, created_by=user.id)
    db.add(workflow)
    db.commit()
    db.refresh(workflow)

    # Set the id BEFORE dumping. Previously the dump happened first, so every
    # stored graph carried "workflow_id": "stub".
    graph.workflow_id = str(workflow.id)
    workflow.graph = graph.model_dump()
    db.commit()
    return graph


@router.get("/")
def list_workflows(db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """Every workflow in the caller's org, newest first."""
    rows = (db.query(Workflow)
            .filter_by(org_id=user.org_id)
            .order_by(Workflow.created_at.desc()).all())
    return [{"id": str(w.id), "query": w.query, "status": w.status,
             "created_at": w.created_at} for w in rows]


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    wf = _owned_workflow(db, workflow_id, user)
    return {
        "id": str(wf.id),
        "query": wf.query,
        "aoi": wf.aoi,
        "date_range": wf.date_range,
        "graph": wf.graph,
        "status": wf.status,
    }


@router.post("/{workflow_id}/run", response_model=WorkflowRunOut)
def run_workflow(workflow_id: str, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    wf = _owned_workflow(db, workflow_id, user)
    _validate_or_422(wf.graph)

    run = WorkflowRun(
        workflow_id=wf.id,
        status="pending",
        params_snapshot={"graph": wf.graph, "aoi": wf.aoi, "date_range": wf.date_range},
        created_by=user.id,
    )
    db.add(run)
    wf.status = "running"
    db.commit()
    db.refresh(run)

    # ONE task for the whole graph, not one per node: the orchestrator walks
    # the DAG in topological order and honours dependencies.
    run_workflow_task.delay(str(run.id))

    return WorkflowRunOut(id=str(run.id), workflow_id=str(wf.id), status=run.status)


def _latest_run(db: Session, workflow_id) -> WorkflowRun | None:
    return (db.query(WorkflowRun)
            .filter_by(workflow_id=workflow_id)
            .order_by(WorkflowRun.created_at.desc())
            .first())


def _run_out(db: Session, run: WorkflowRun) -> WorkflowRunOut:
    node_runs = db.query(NodeRun).filter_by(workflow_run_id=run.id).all()
    return WorkflowRunOut(
        id=str(run.id), workflow_id=str(run.workflow_id),
        status=run.status, error=run.error,
        nodes=[NodeRunStatus(node_id=nr.node_id, node_type=nr.node_type,
                             status=nr.status, output_ref=nr.output_ref,
                             error=nr.error, started_at=nr.started_at,
                             finished_at=nr.finished_at)
               for nr in node_runs],
    )


@router.get("/{workflow_id}/status", response_model=WorkflowRunOut)
def get_workflow_status(workflow_id: str, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    wf = _owned_workflow(db, workflow_id, user)
    run = _latest_run(db, wf.id)
    if not run:
        raise HTTPException(404, "No runs for this workflow")
    return _run_out(db, run)


@router.get("/{workflow_id}/runs")
def list_runs(workflow_id: str, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    wf = _owned_workflow(db, workflow_id, user)
    runs = (db.query(WorkflowRun)
            .filter_by(workflow_id=wf.id)
            .order_by(WorkflowRun.created_at.desc()).all())
    return [{"id": str(r.id), "status": r.status, "created_at": r.created_at,
             "started_at": r.started_at, "finished_at": r.finished_at,
             "error": r.error} for r in runs]