"""alerts: an incident's private picture and box, when it closed, its camera in its own society, and the Telegram
messages posted about it

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('picture_key', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('picture_box', JSON, nullable=True))
        batch_op.add_column(sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_unique_constraint('uq_incidents_ws_id', ['workspace_id', 'id'])
        batch_op.create_foreign_key('fk_incidents_camera_id_cameras', 'cameras', ['workspace_id', 'camera_id'],
                                    ['workspace_id', 'id'], ondelete='CASCADE')

    op.create_table('telegram_messages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('incident_id', sa.String(), nullable=False),
    sa.Column('chat_id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('photo', sa.Boolean(), nullable=False),
    sa.Column('shown', sa.String(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id', 'incident_id'], ['incidents.workspace_id', 'incidents.id'], name='fk_telegram_messages_incident_id_incidents', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_telegram_messages_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_messages')),
    sa.UniqueConstraint('chat_id', 'message_id', name='uq_telegram_messages_chat_message')
    )
    with op.batch_alter_table('telegram_messages', schema=None) as batch_op:
        batch_op.create_index('ix_telegram_messages_ws_incident', ['workspace_id', 'incident_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('telegram_messages', schema=None) as batch_op:
        batch_op.drop_index('ix_telegram_messages_ws_incident')

    op.drop_table('telegram_messages')
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_incidents_camera_id_cameras', type_='foreignkey')
        batch_op.drop_constraint('uq_incidents_ws_id', type_='unique')
        batch_op.drop_column('closed_at')
        batch_op.drop_column('picture_box')
        batch_op.drop_column('picture_key')
