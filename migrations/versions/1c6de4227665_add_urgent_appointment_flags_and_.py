"""add urgent appointment flags and caregiver onboarding

Revision ID: 1c6de4227665
Revises: c5e94a48ff0d
Create Date: 2026-06-02 18:27:25.990982

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1c6de4227665'
down_revision = 'c5e94a48ff0d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('appointment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('urgent_flag', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('urgent_reason', sa.String(length=220), nullable=True))

    op.create_table(
        'caregiver_onboarding_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('caregiver_id', sa.Integer(), nullable=False),
        sa.Column('item_type', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=40), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['caregiver_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('caregiver_onboarding_item')
    with op.batch_alter_table('appointment', schema=None) as batch_op:
        batch_op.drop_column('urgent_reason')
        batch_op.drop_column('urgent_flag')


