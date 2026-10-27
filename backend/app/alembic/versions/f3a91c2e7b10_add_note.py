"""Add owner-scoped study notes.

Revision ID: f3a91c2e7b10
Revises: e8c1a4d72b90
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a91c2e7b10"
down_revision = "e8c1a4d72b90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "note",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_owner_id", "note", ["owner_id"])
    op.create_index("ix_note_owner_updated_at", "note", ["owner_id", "updated_at"])


def downgrade():
    op.drop_index("ix_note_owner_updated_at", table_name="note")
    op.drop_index("ix_note_owner_id", table_name="note")
    op.drop_table("note")
