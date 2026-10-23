"""Add owner-scoped documents and chunks.

Revision ID: a2d6c8401b17
Revises: fe56fa70289e
"""

from alembic import op
import sqlalchemy as sa

revision = "a2d6c8401b17"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "sha256", name="uq_document_owner_sha256"),
    )
    op.create_index("ix_document_owner_id", "document", ["owner_id"])
    op.create_table(
        "documentchunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(255), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "position", name="uq_documentchunk_position"),
    )
    op.create_index("ix_documentchunk_document_id", "documentchunk", ["document_id"])


def downgrade():
    op.drop_index("ix_documentchunk_document_id", table_name="documentchunk")
    op.drop_table("documentchunk")
    op.drop_index("ix_document_owner_id", table_name="document")
    op.drop_table("document")
