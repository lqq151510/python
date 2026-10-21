"""Add owner-scoped image library and web agent sessions.

Revision ID: e8c1a4d72b90
Revises: b7c4a1d9e2f0
"""

import sqlalchemy as sa
from alembic import op

revision = "e8c1a4d72b90"
down_revision = "b7c4a1d9e2f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "imageasset",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "sha256", name="uq_imageasset_owner_sha256"),
    )
    op.create_index("ix_imageasset_owner_id", "imageasset", ["owner_id"])
    op.create_table(
        "agentsession",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agentsession_owner_id", "agentsession", ["owner_id"])
    op.create_index(
        "ix_agentsession_owner_updated_at",
        "agentsession",
        ["owner_id", "updated_at"],
    )
    op.create_table(
        "agentturn",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("stopped", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agentsession.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_agentturn_request_id"),
    )
    op.create_index("ix_agentturn_session_id", "agentturn", ["session_id"])
    op.create_index("ix_agentturn_owner_id", "agentturn", ["owner_id"])
    op.create_index(
        "ix_agentturn_owner_session_created",
        "agentturn",
        ["owner_id", "session_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_agentturn_owner_session_created", table_name="agentturn")
    op.drop_index("ix_agentturn_owner_id", table_name="agentturn")
    op.drop_index("ix_agentturn_session_id", table_name="agentturn")
    op.drop_table("agentturn")
    op.drop_index("ix_agentsession_owner_updated_at", table_name="agentsession")
    op.drop_index("ix_agentsession_owner_id", table_name="agentsession")
    op.drop_table("agentsession")
    op.drop_index("ix_imageasset_owner_id", table_name="imageasset")
    op.drop_table("imageasset")
