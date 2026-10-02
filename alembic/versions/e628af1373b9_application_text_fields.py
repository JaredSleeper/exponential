"""Allow unbounded application referrer and role/company text

Revision ID: e628af1373b9
Revises: d991e45fc730
Create Date: 2026-10-02

"""
import sqlalchemy as sa

from alembic import op

revision = "e628af1373b9"
down_revision = "d991e45fc730"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.alter_column(
            "role_company",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "referrer",
            existing_type=sa.String(length=160),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.alter_column(
            "role_company",
            existing_type=sa.Text(),
            type_=sa.String(length=200),
            existing_nullable=False,
            postgresql_using="left(role_company, 200)",
        )
        batch_op.alter_column(
            "referrer",
            existing_type=sa.Text(),
            type_=sa.String(length=160),
            existing_nullable=False,
            postgresql_using="left(referrer, 160)",
        )
