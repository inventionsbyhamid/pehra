"""billing: watched cameras paid for and comped, the period, and how long footage saved under a longer plan is kept

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (('subscription_period', sa.String()), ('subscription_units', sa.Integer()),
           ('subscription_units_next', sa.Integer()), ('subscription_period_start', sa.DateTime(timezone=True)),
           ('comp_cameras', sa.Integer()), ('retention_hold_days', sa.Integer()),
           ('retention_hold_until', sa.DateTime(timezone=True)))


def upgrade() -> None:
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        for name, kind in COLUMNS:
            batch_op.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        for name, _ in reversed(COLUMNS):
            batch_op.drop_column(name)
