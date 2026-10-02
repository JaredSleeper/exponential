"""Add editable public site copy

Revision ID: d991e45fc730
Revises: b7c2e4d19a01
Create Date: 2026-10-02

"""
import sqlalchemy as sa

from alembic import op

revision = "d991e45fc730"
down_revision = "b7c2e4d19a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_copy",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=320), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.alter_column(
            "target_id",
            existing_type=sa.String(length=40),
            type_=sa.String(length=80),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.alter_column(
            "target_id",
            existing_type=sa.String(length=80),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
    op.drop_table("site_copy")
