"""billing: who paid for a society's plan, and why a camera was switched off (the plan watches fewer)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('billing_user_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('billing_email', sa.String(), nullable=True))
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.add_column(sa.Column('off_reason', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.drop_column('off_reason')
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.drop_column('billing_email')
        batch_op.drop_column('billing_user_id')
