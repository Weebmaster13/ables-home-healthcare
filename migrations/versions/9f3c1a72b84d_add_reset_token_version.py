"""add reset token version for atomic single-use enforcement

Revision ID: 9f3c1a72b84d
Revises: 7b2f4d31a9ce
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "9f3c1a72b84d"
down_revision = "7b2f4d31a9ce"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "reset_token_version", sa.Integer(),
            nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("reset_token_version")
