"""Rename applications.link to linkedin

Revision ID: b7c2e4d19a01
Revises: 9411b135d809
Create Date: 2026-09-28 16:30:00

"""
from alembic import op

revision = 'b7c2e4d19a01'
down_revision = '9411b135d809'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('applications') as batch:
        batch.alter_column('link', new_column_name='linkedin')


def downgrade() -> None:
    with op.batch_alter_table('applications') as batch:
        batch.alter_column('linkedin', new_column_name='link')
