"""Add AIGC video generation jobs.

Revision ID: b7c4a1d9e2f0
Revises: c7d3e18a04f2
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c4a1d9e2f0"
down_revision = "c7d3e18a04f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aigcjob",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status_message", sa.String(length=120), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("comfy_prompt_id", sa.String(length=64), nullable=True),
        sa.Column("input_image_relpath", sa.String(length=512), nullable=True),
        sa.Column("input_image_name", sa.String(length=255), nullable=True),
        sa.Column("output_relpath", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aigcjob_owner_id", "aigcjob", ["owner_id"])
    op.create_index("ix_aigcjob_status", "aigcjob", ["status"])
    op.create_index(
        "ix_aigcjob_owner_created_at", "aigcjob", ["owner_id", "created_at"]
    )
    op.create_index(
        "ix_aigcjob_status_created_at", "aigcjob", ["status", "created_at"]
    )


def downgrade():
    op.drop_index("ix_aigcjob_status_created_at", table_name="aigcjob")
    op.drop_index("ix_aigcjob_owner_created_at", table_name="aigcjob")
    op.drop_index("ix_aigcjob_status", table_name="aigcjob")
    op.drop_index("ix_aigcjob_owner_id", table_name="aigcjob")
    op.drop_table("aigcjob")
