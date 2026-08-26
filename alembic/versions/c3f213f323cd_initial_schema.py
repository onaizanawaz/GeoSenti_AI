"""initial schema

Revision ID: c3f213f323cd
Revises:
Create Date: 2026-08-26 18:57:26.191654

Hand-edited after autogenerate:
  1. Added the missing `import geoalchemy2` (autogenerate emits the type
     reference but not the import -> NameError on run).
  2. Added CREATE EXTENSION postgis as the first statement; autogenerate
     never emits it, and the Geometry column cannot be created without it.
  3. Removed op.create_index('idx_field_boundaries_geom'); geoalchemy2
     creates that index itself as a side effect of the Geometry column,
     so running it again raises "index already exists".
  4. Removed op.drop_table('spatial_ref_sys') / its recreation in
     downgrade(). That table belongs to PostGIS, not to this app.
"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3f213f323cd'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        'field_boundaries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('geom', geoalchemy2.types.Geometry(
            geometry_type='POLYGON', srid=4326, dimension=2,
            from_text='ST_GeomFromEWKT', name='geometry', nullable=False),
            nullable=False),
        sa.Column('farm_owner', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # NOTE: no create_index for 'geom' -- geoalchemy2 emits the GiST index
    # itself when the Geometry column is created.

    op.create_table(
        'node_catalog',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('type_name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('input_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('output_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('param_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('implementation_ref', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('type_name'),
    )

    op.create_table(
        'workflows',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('aoi', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('date_range', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('graph', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'node_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('workflow_id', sa.UUID(), nullable=False),
        sa.Column('node_id', sa.String(), nullable=False),
        sa.Column('node_type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('output_ref', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema.

    The postgis extension is deliberately NOT dropped: other databases in the
    cluster may depend on it, and dropping it would cascade-delete geometry
    columns elsewhere.
    """
    op.drop_table('node_runs')
    op.drop_table('workflows')
    op.drop_table('node_catalog')
    op.drop_table('field_boundaries')