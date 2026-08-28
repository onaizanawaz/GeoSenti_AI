"""Per-node Celery task.

Unused until Phase 5, which replaces the orchestrator's sequential loop with
wave dispatch (group(execute_node.s(...)) plus a self-rescheduling orchestrator).
Until then the orchestrator calls execute_one() directly, in-process.

The old _run_node_logic dispatcher lived here and raised NotImplementedError
for every node type; node implementations now live in app/services/nodes/.
"""

import logging

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Workflow, WorkflowRun
from app.services.orchestrator import execute_one
from app.services.storage import get_store

log = logging.getLogger(__name__)


@celery_app.task(name="execute_node")
def execute_node(workflow_run_id: str, node_id: str) -> str:
    db = SessionLocal()
    try:
        run = db.query(WorkflowRun).filter_by(id=workflow_run_id).first()
        if run is None:
            # Previously this dereferenced a None node_run in both the body and
            # the except handler, so the task died and the row stayed "pending".
            log.error("WorkflowRun %s not found", workflow_run_id)
            return "missing"

        wf = db.query(Workflow).filter_by(id=run.workflow_id).first()
        node = next((n for n in wf.graph["nodes"] if n["id"] == node_id), None)
        if node is None:
            log.error("Node %s not present in workflow %s", node_id, wf.id)
            return "missing"

        return execute_one(db, get_store(), run, wf, wf.graph, node)
    finally:
        db.close()