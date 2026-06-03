"""add caregiver application requirements

Revision ID: 7b2f4d31a9ce
Revises: 1c6de4227665
Create Date: 2026-06-03 18:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "7b2f4d31a9ce"
down_revision = "1c6de4227665"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("caregiver_application", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requirements_checklist", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("caregiver_application", schema=None) as batch_op:
        batch_op.drop_column("requirements_checklist")
