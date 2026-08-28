"""The node catalog as the frontend and the Phase 6 LLM planner both see it.

Hidden nodes (the dummy test nodes) are excluded, so the planner can never
emit them.
"""

from fastapi import APIRouter

from app.schemas import NodeCatalogOut
from app.services.node_catalog import catalog_entries

router = APIRouter(tags=["catalog"])


@router.get("/catalog", response_model=list[NodeCatalogOut])
def get_catalog():
    return [NodeCatalogOut(**e) for e in catalog_entries(include_hidden=False)]