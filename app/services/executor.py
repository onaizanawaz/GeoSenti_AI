"""
Execution layer. Walks a workflow graph in topological order and runs
each node against its real implementation (GEE, WaPOR, etc).
This is a stub, fill in real GEE/WaPOR calls per node_type as you build them.
"""

from datetime import datetime

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import NodeRun


@celery_app.task(name="execute_node")
def execute_node(node_run_id: str, node_type: str, params: dict):
    db = SessionLocal()
    node_run = db.query(NodeRun).filter_by(id=node_run_id).first()
    try:
        node_run.status = "running"
        node_run.started_at = datetime.utcnow()
        db.commit()

        # Dispatch to the real implementation. Replace with actual node logic.
        output_ref = _run_node_logic(node_type, params)

        node_run.status = "done"
        node_run.output_ref = output_ref
        node_run.finished_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        node_run.status = "failed"
        node_run.error = str(e)
        node_run.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_node_logic(node_type: str, params: dict) -> str:
    """
    Placeholder dispatcher. Replace each branch with the real call
    (e.g. gee.fetch_sentinel2(**params), wapor.fetch_et(**params)).
    Returns a reference to where the output was stored.
    """
    if node_type == "fetch_sentinel2":
        raise NotImplementedError("Wire up GEE Sentinel-2 fetch here")
    elif node_type == "compute_ndvi":
        raise NotImplementedError("Wire up NDVI computation here")
    # ... add the rest of the node_catalog types here
    raise NotImplementedError(f"No implementation registered for node type: {node_type}")