import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Workflow, NodeRun
from app.schemas import WorkflowCreate, WorkflowGraph, NodeRunStatus
from app.services.planner import generate_graph
from app.services.executor import execute_node

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/", response_model=WorkflowGraph)
def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db)):
    graph = generate_graph(payload.query, payload.aoi, payload.date_range)

    workflow = Workflow(
        query=payload.query,
        aoi=payload.aoi,
        date_range=payload.date_range,
        graph=graph.model_dump(),
        status="draft",
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)

    graph.workflow_id = str(workflow.id)
    return graph


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db)):
    workflow = db.query(Workflow).filter_by(id=workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {
        "id": str(workflow.id),
        "query": workflow.query,
        "graph": workflow.graph,
        "status": workflow.status,
    }


@router.post("/{workflow_id}/run")
def run_workflow(workflow_id: str, db: Session = Depends(get_db)):
    workflow = db.query(Workflow).filter_by(id=workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    workflow.status = "running"
    db.commit()

    node_run_ids = []
    for node in workflow.graph["nodes"]:
        node_run = NodeRun(
            workflow_id=workflow.id,
            node_id=node["id"],
            node_type=node["type"],
            status="pending",
        )
        db.add(node_run)
        db.commit()
        db.refresh(node_run)
        node_run_ids.append(str(node_run.id))

        # NOTE: this dispatches all nodes immediately for now.
        # Real topological ordering/dependency waiting goes here once
        # more than one node type is actually implemented.
        execute_node.delay(str(node_run.id), node["type"], node.get("params", {}))

    return {"workflow_id": workflow_id, "node_run_ids": node_run_ids, "status": "running"}


@router.get("/{workflow_id}/status", response_model=list[NodeRunStatus])
def get_workflow_status(workflow_id: str, db: Session = Depends(get_db)):
    node_runs = db.query(NodeRun).filter_by(workflow_id=workflow_id).all()
    return [
        NodeRunStatus(
            node_id=nr.node_id,
            node_type=nr.node_type,
            status=nr.status,
            output_ref=nr.output_ref,
            error=nr.error,
        )
        for nr in node_runs
    ]