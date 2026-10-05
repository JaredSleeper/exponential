"""Add AI scores to applications and members."""

import sqlalchemy as sa

from alembic import op

revision = "e150e1e4cd16"
down_revision = "e628af1373b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("score", sa.Integer(), nullable=True))
    op.add_column(
        "applications", sa.Column("score_source", sa.String(length=8), server_default="", nullable=False)
    )
    op.add_column("applications", sa.Column("score_reason", sa.Text(), server_default="", nullable=False))
    op.add_column("applications", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("members", sa.Column("score", sa.Integer(), nullable=True))
    op.add_column("members", sa.Column("score_source", sa.String(length=8), server_default="", nullable=False))
    op.add_column("members", sa.Column("score_reason", sa.Text(), server_default="", nullable=False))
    op.add_column("members", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "scored_at")
    op.drop_column("members", "score_reason")
    op.drop_column("members", "score_source")
    op.drop_column("members", "score")
    op.drop_column("applications", "scored_at")
    op.drop_column("applications", "score_reason")
    op.drop_column("applications", "score_source")
    op.drop_column("applications", "score")
