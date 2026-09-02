"""Artifact listing and download.

Downloads stream through the store rather than exposing a filesystem path.
That also makes this the single chokepoint for the org ownership check: an
artifact reaches its org only by joining run -> workflow, and _owned_artifact()
is the one place that join is written.
"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Artifact, User, Workflow, WorkflowRun
from app.schemas import ArtifactOut
from app.services.auth import current_user
from app.services.storage import get_store

router = APIRouter(tags=["artifacts"])

MEDIA = {
    "raster": "image/tiff",
    "json": "application/json",
    "scalar": "application/json",
    "vector": "application/geo+json",
    "table": "text/csv",
    "ee_object": "application/json",
}


def _out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(id=str(a.id), node_id=a.node_id, name=a.name,
                       kind=a.kind, uri=a.uri, meta=a.meta or {})


def _as_uuid(value: str, what: str):
    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, f"{what} not found")


def _owned_run(db: Session, run_id: str, user: User) -> WorkflowRun:
    run = (db.query(WorkflowRun)
           .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
           .filter(WorkflowRun.id == _as_uuid(run_id, "Run"),
                   Workflow.org_id == user.org_id)
           .first())
    if not run:
        raise HTTPException(404, "Run not found")
    return run


def _owned_artifact(db: Session, artifact_id: str, user: User) -> Artifact:
    """404 rather than 403 for another org's artifact: a 403 would confirm the
    id exists, which is exactly the fact being protected."""
    a = (db.query(Artifact)
         .join(WorkflowRun, WorkflowRun.id == Artifact.workflow_run_id)
         .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
         .filter(Artifact.id == _as_uuid(artifact_id, "Artifact"),
                 Workflow.org_id == user.org_id)
         .first())
    if not a:
        raise HTTPException(404, "Artifact not found")
    return a


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(run_id: str, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    run = _owned_run(db, run_id, user)
    return [_out(a) for a in
            db.query(Artifact).filter_by(workflow_run_id=run.id).all()]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: str, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    return _out(_owned_artifact(db, artifact_id, user))


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    a = _owned_artifact(db, artifact_id, user)
    try:
        path = get_store().fetch(a.uri)
    except FileNotFoundError:
        raise HTTPException(410, "Artifact file is no longer on disk")
    return FileResponse(path,
                        media_type=MEDIA.get(a.kind, "application/octet-stream"),
                        filename=path.name)