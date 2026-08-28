"""Artifact listing and download.

Downloads stream through the store rather than exposing a filesystem path.
That also makes this the single chokepoint where Phase 7 adds the org
ownership check.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Artifact
from app.schemas import ArtifactOut
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


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(run_id: str, db: Session = Depends(get_db)):
    return [_out(a) for a in
            db.query(Artifact).filter_by(workflow_run_id=run_id).all()]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    a = db.query(Artifact).filter_by(id=artifact_id).first()
    if not a:
        raise HTTPException(404, "Artifact not found")
    return _out(a)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)):
    a = db.query(Artifact).filter_by(id=artifact_id).first()
    if not a:
        raise HTTPException(404, "Artifact not found")
    # Phase 7: assert the run's org matches current_user's org before serving.
    try:
        path = get_store().fetch(a.uri)
    except FileNotFoundError:
        raise HTTPException(410, "Artifact file is no longer on disk")
    return FileResponse(path,
                        media_type=MEDIA.get(a.kind, "application/octet-stream"),
                        filename=path.name)