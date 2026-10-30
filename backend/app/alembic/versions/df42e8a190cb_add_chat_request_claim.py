"""Add durable claims for owner-scoped chat requests.

Revision ID: df42e8a190cb
Revises: c4e8b1a09d22
"""

import sqlalchemy as sa
from alembic import op

revision = "df42e8a190cb"
down_revision = "c4e8b1a09d22"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chatrequestclaim",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "client_request_id", name="uq_chatclaim_owner_client_request"),
    )


def downgrade():
    op.drop_table("chatrequestclaim")
