"""Add owner-scoped study tasks.

Revision ID: c4e8b1a09d22
Revises: f3a91c2e7b10
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8b1a09d22"
down_revision = "f3a91c2e7b10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "studytask",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_studytask_owner_id", "studytask", ["owner_id"])
    op.create_index("ix_studytask_owner_done", "studytask", ["owner_id", "done"])


def downgrade():
    op.drop_index("ix_studytask_owner_done", table_name="studytask")
    op.drop_index("ix_studytask_owner_id", table_name="studytask")
    op.drop_table("studytask")
