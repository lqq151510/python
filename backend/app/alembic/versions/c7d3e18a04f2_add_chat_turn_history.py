"""Add owner-scoped chat history.

Revision ID: c7d3e18a04f2
Revises: a2d6c8401b17
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d3e18a04f2"
down_revision = "a2d6c8401b17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chatturn",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("retrieval_method", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieval_warning", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "client_request_id", name="uq_chatturn_owner_client_request"
        ),
        sa.UniqueConstraint("request_id", name="uq_chatturn_request_id"),
    )
    op.create_index(
        "ix_chatturn_owner_created_at", "chatturn", ["owner_id", "created_at"]
    )


def downgrade():
    op.drop_index("ix_chatturn_owner_created_at", table_name="chatturn")
    op.drop_table("chatturn")
