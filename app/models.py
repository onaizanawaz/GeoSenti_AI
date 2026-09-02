import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geometry

from app.database import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now. datetime.utcnow() is deprecated and returns a
    naive datetime, which silently loses the offset on write."""
    return datetime.now(timezone.utc)


class Org(Base):
    """A tenant. Every workflow belongs to exactly one, and every read is
    scoped by it -- the org is the security boundary, not the user."""
    __tablename__ = "orgs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class User(Base):
    """A login. Users never span orgs: a person needing access to two orgs
    gets two users, which keeps every query a single equality check."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("orgs.id"),
                    nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="member", nullable=False)   # owner | member
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class NodeCatalog(Base):
    """The registered list of GIS operations the planner LLM is allowed to use.
    Rows are derived from NODE_REGISTRY in code -- see services/node_catalog.py."""
    __tablename__ = "node_catalog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_name = Column(String, unique=True, nullable=False)  # e.g. "compute_ndvi"
    description = Column(Text, nullable=False)
    input_schema = Column(JSONB, default=dict)
    output_schema = Column(JSONB, default=dict)
    param_schema = Column(JSONB, default=dict)
    implementation_ref = Column(String, nullable=False)


class Workflow(Base):
    """One NL query and the graph generated from it."""
    __tablename__ = "workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query = Column(Text, nullable=False)
    aoi = Column(JSONB, default=dict)
    date_range = Column(JSONB, default=dict)
    graph = Column(JSONB, default=dict)  # the full generated DAG
    status = Column(String, default="draft")  # draft / running / done / failed
    field_boundary_id = Column(UUID(as_uuid=True),
                               ForeignKey("field_boundaries.id"), nullable=True)
    # The tenancy anchor. Runs, node_runs and artifacts all reach their org
    # through this column, so there is exactly one place to get it right.
    org_id = Column(UUID(as_uuid=True), ForeignKey("orgs.id"),
                    nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowRun(Base):
    """One execution attempt of a workflow. Re-running creates a new row, so
    history is preserved instead of being overwritten."""
    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"),
                         nullable=False, index=True)
    status = Column(String, default="pending")  # pending/running/done/failed/cancelled
    params_snapshot = Column(JSONB, default=dict)  # graph + aoi + date_range as executed
    error = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class NodeRun(Base):
    """Execution status of one node within one workflow run."""
    __tablename__ = "node_runs"
    __table_args__ = (
        # Without this, re-running appends a second full set of rows and
        # /status returns both, interleaved and stale.
        UniqueConstraint("workflow_run_id", "node_id", name="uq_noderun_run_node"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.id"),
                             nullable=False, index=True)
    node_id = Column(String, nullable=False)     # matches the id inside graph JSON
    node_type = Column(String, nullable=False)
    status = Column(String, default="pending")   # pending/running/done/failed/skipped/cancelled
    output_ref = Column(String, nullable=True)   # uri of the primary artifact
    attempt = Column(Integer, default=1)
    logs = Column(Text, nullable=True)           # truncated traceback on failure
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)


class Artifact(Base):
    """One named output produced by one node run. A node may produce several."""
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("node_run_id", "name", name="uq_artifact_noderun_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.id"),
                             nullable=False, index=True)
    node_run_id = Column(UUID(as_uuid=True), ForeignKey("node_runs.id"),
                         nullable=False, index=True)
    node_id = Column(String, nullable=False)
    name = Column(String, nullable=False)   # logical port name, e.g. "ndvi"
    kind = Column(String, nullable=False)   # raster|vector|table|json|scalar|ee_object
    uri = Column(String, nullable=False)
    meta = Column(JSONB, default=dict)      # crs, bounds, dtype, stats, image_count, ...
    created_at = Column(DateTime(timezone=True), default=utcnow)


class FieldBoundary(Base):
    """Farm/field polygons used as AOIs for workflows."""
    __tablename__ = "field_boundaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    geom = Column(Geometry("POLYGON", srid=4326), nullable=False)
    farm_owner = Column(String, nullable=True)