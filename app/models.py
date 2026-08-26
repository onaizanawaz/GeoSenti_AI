import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geometry

from app.database import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now. datetime.utcnow() is deprecated and returns a
    naive datetime, which silently loses the offset on write."""
    return datetime.now(timezone.utc)


class NodeCatalog(Base):
    """The registered list of GIS operations the planner LLM is allowed to use."""
    __tablename__ = "node_catalog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_name = Column(String, unique=True, nullable=False)  # e.g. "compute_ndvi"
    description = Column(Text, nullable=False)
    input_schema = Column(JSONB, default=dict)
    output_schema = Column(JSONB, default=dict)
    param_schema = Column(JSONB, default=dict)
    implementation_ref = Column(String, nullable=False)  # e.g. path to the GEE function


class Workflow(Base):
    """One NL query and the graph generated from it."""
    __tablename__ = "workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query = Column(Text, nullable=False)
    aoi = Column(JSONB, default=dict)
    date_range = Column(JSONB, default=dict)
    graph = Column(JSONB, default=dict)  # the full generated DAG
    status = Column(String, default="draft")  # draft / running / done / failed
    created_at = Column(DateTime(timezone=True), default=utcnow)


class NodeRun(Base):
    """Execution status of one node within one workflow run."""
    __tablename__ = "node_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)
    node_id = Column(String, nullable=False)     # matches the id inside graph JSON
    node_type = Column(String, nullable=False)
    status = Column(String, default="pending")   # pending / running / done / failed
    output_ref = Column(String, nullable=True)   # pointer to result in object storage
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)


class FieldBoundary(Base):
    """Farm/field polygons used as AOIs for workflows."""
    __tablename__ = "field_boundaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    geom = Column(Geometry("POLYGON", srid=4326), nullable=False)
    farm_owner = Column(String, nullable=True)