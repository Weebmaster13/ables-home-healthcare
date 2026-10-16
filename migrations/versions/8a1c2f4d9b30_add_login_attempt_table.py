"""add login_attempt table for shared rate limiting

Revision ID: 8a1c2f4d9b30
Revises: 7b2f4d31a9ce
Create Date: 2026-10-16 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '8a1c2f4d9b30'
down_revision = '7b2f4d31a9ce'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'login_attempt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=120), nullable=False),
        sa.Column('attempted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('login_attempt', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_attempt_key'), ['key'], unique=False)
        batch_op.create_index(batch_op.f('ix_login_attempt_attempted_at'), ['attempted_at'], unique=False)


def downgrade():
    with op.batch_alter_table('login_attempt', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_attempt_attempted_at'))
        batch_op.drop_index(batch_op.f('ix_login_attempt_key'))
    op.drop_table('login_attempt')
