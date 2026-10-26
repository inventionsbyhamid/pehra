"""logs: vehicle passages point at a camera of their own society and keep their line, track, reads and the stay
watches that fired; staff keep the consent words and who removed them and when

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # a passage whose camera is gone or in another society can't keep pointing at it
    op.execute(sa.text(
        "DELETE FROM vehicle_passages WHERE NOT EXISTS (SELECT 1 FROM cameras c WHERE c.id = vehicle_passages.camera_id"
        " AND c.workspace_id = vehicle_passages.workspace_id)"))
    with op.batch_alter_table('vehicle_passages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('line', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('track_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('reads_json', JSON, nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('alerted_json', JSON, nullable=False, server_default='[]'))
        batch_op.create_foreign_key('fk_vehicle_passages_camera_id_cameras', 'cameras', ['workspace_id', 'camera_id'],
                                    ['workspace_id', 'id'], ondelete='CASCADE')
    with op.batch_alter_table('vehicle_passages', schema=None) as batch_op:
        batch_op.alter_column('reads_json', server_default=None)
        batch_op.alter_column('alerted_json', server_default=None)
    with op.batch_alter_table('staff', schema=None) as batch_op:
        batch_op.add_column(sa.Column('consent_text', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('removed_by', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('staff', schema=None) as batch_op:
        batch_op.drop_column('removed_by')
        batch_op.drop_column('removed_at')
        batch_op.drop_column('consent_text')
    with op.batch_alter_table('vehicle_passages', schema=None) as batch_op:
        batch_op.drop_constraint('fk_vehicle_passages_camera_id_cameras', type_='foreignkey')
        batch_op.drop_column('alerted_json')
        batch_op.drop_column('reads_json')
        batch_op.drop_column('track_id')
        batch_op.drop_column('line')
