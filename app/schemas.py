from pydantic import BaseModel


class NodeSpec(BaseModel):
    id: str
    type: str
    params: dict = {}
    inputs: list[str] = []
    outputs: list[str] = []


class WorkflowCreate(BaseModel):
    query: str
    aoi: dict
    date_range: dict


class WorkflowGraph(BaseModel):
    workflow_id: str
    nodes: list[NodeSpec]
    edges: list[tuple[str, str]]


class NodeRunStatus(BaseModel):
    node_id: str
    node_type: str
    status: str
    output_ref: str | None = None
    error: str | None = None