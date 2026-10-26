"""attendance: first and last seen (sightings are not ins and outs), and the crossings of a directed line in and out

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.alter_column('first_in', new_column_name='first_seen')
        batch_op.alter_column('last_out', new_column_name='last_seen')
        batch_op.add_column(sa.Column('came_in', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('went_out', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.drop_column('went_out')
        batch_op.drop_column('came_in')
        batch_op.alter_column('last_seen', new_column_name='last_out')
        batch_op.alter_column('first_seen', new_column_name='first_in')
