from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import NodeRun, Workflow, WorkflowRun
from app.schemas import (NodeRunStatus, WorkflowCreate, WorkflowGraph,
                         WorkflowRunOut)
from app.services.dag import blocking, validate_graph
from app.services.nodes import load_registry
from app.services.orchestrator import run_workflow_task
from app.services.planner import generate_graph

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _validate_or_422(graph):
    errors = blocking(validate_graph(graph, load_registry()))
    if errors:
        raise HTTPException(status_code=422, detail=[e.dict() for e in errors])


@router.post("/", response_model=WorkflowGraph)
def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db)):
    graph = generate_graph(payload.query, payload.aoi, payload.date_range)
    _validate_or_422(graph)

    workflow = Workflow(query=payload.query, aoi=payload.aoi,
                        date_range=payload.date_range, graph={}, status="draft")
    db.add(workflow)
    db.commit()
    db.refresh(workflow)

    # Set the id BEFORE dumping. Previously the dump happened first, so every
    # stored graph carried "workflow_id": "stub".
    graph.workflow_id = str(workflow.id)
    workflow.graph = graph.model_dump()
    db.commit()
    return graph


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db)):
    wf = db.query(Workflow).filter_by(id=workflow_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return {
        "id": str(wf.id),
        "query": wf.query,
        "aoi": wf.aoi,
        "date_range": wf.date_range,
        "graph": wf.graph,
        "status": wf.status,
    }


@router.post("/{workflow_id}/run", response_model=WorkflowRunOut)
def run_workflow(workflow_id: str, db: Session = Depends(get_db)):
    wf = db.query(Workflow).filter_by(id=workflow_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    _validate_or_422(wf.graph)

    run = WorkflowRun(
        workflow_id=wf.id,
        status="pending",
        params_snapshot={"graph": wf.graph, "aoi": wf.aoi, "date_range": wf.date_range},
    )
    db.add(run)
    wf.status = "running"
    db.commit()
    db.refresh(run)

    # ONE task for the whole graph, not one per node: the orchestrator walks
    # the DAG in topological order and honours dependencies.
    run_workflow_task.delay(str(run.id))

    return WorkflowRunOut(id=str(run.id), workflow_id=str(wf.id), status=run.status)


def _latest_run(db: Session, workflow_id: str) -> WorkflowRun | None:
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
def get_workflow_status(workflow_id: str, db: Session = Depends(get_db)):
    run = _latest_run(db, workflow_id)
    if not run:
        raise HTTPException(404, "No runs for this workflow")
    return _run_out(db, run)


@router.get("/{workflow_id}/runs")
def list_runs(workflow_id: str, db: Session = Depends(get_db)):
    runs = (db.query(WorkflowRun)
            .filter_by(workflow_id=workflow_id)
            .order_by(WorkflowRun.created_at.desc()).all())
    return [{"id": str(r.id), "status": r.status, "created_at": r.created_at,
             "started_at": r.started_at, "finished_at": r.finished_at,
             "error": r.error} for r in runs]