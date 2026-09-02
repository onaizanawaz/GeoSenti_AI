from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class NodeSpec(BaseModel):
    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    """source_output/target_input are unused until the Phase 6 LLM planner, but
    the JSONB shape is far cheaper to settle now than once there is data."""
    source: str
    target: str
    source_output: str | None = None
    target_input: str | None = None


class WorkflowGraph(BaseModel):
    workflow_id: str
    nodes: list[NodeSpec]
    edges: list[Edge]


class WorkflowCreate(BaseModel):
    query: str
    aoi: dict[str, Any]           # GeoJSON geometry or Feature
    date_range: dict[str, str]    # {"start": "2024-01-01", "end": "2024-12-31"}


class RegisterRequest(BaseModel):
    """Registering creates an org and its first owner together. There is no
    such thing as a user without an org, so they are never created apart."""
    email: EmailStr
    password: str
    org_name: str


class InviteRequest(BaseModel):
    """Adding a member to the caller's own org. The org is taken from the
    caller's token, never from the body -- otherwise anyone could add a user
    to someone else's org."""
    email: EmailStr
    password: str
    role: str = "member"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    org_id: str
    org_name: str


class GraphErrorOut(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    hint: str | None = None


class NodeRunStatus(BaseModel):
    node_id: str
    node_type: str
    status: str
    output_ref: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ArtifactOut(BaseModel):
    id: str
    node_id: str
    name: str
    kind: str
    uri: str
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunOut(BaseModel):
    id: str
    workflow_id: str
    status: str
    error: str | None = None
    nodes: list[NodeRunStatus] = Field(default_factory=list)


class NodeCatalogOut(BaseModel):
    type_name: str
    description: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    param_schema: dict[str, str]