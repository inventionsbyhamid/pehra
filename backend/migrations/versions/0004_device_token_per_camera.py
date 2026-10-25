"""device tokens: one per camera, so two links issued at once replace each other instead of both working

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # keep the newest token of each camera, then make the index unique
    op.execute(sa.text(
        "DELETE FROM device_tokens WHERE id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY workspace_id, camera_id ORDER BY COALESCE(rotated_at, created_at) DESC, id DESC) AS n "
        "FROM device_tokens) ranked WHERE n = 1)"))
    op.drop_index('ix_device_tokens_ws_camera', table_name='device_tokens')
    op.create_index('ix_device_tokens_ws_camera', 'device_tokens', ['workspace_id', 'camera_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_device_tokens_ws_camera', table_name='device_tokens')
    op.create_index('ix_device_tokens_ws_camera', 'device_tokens', ['workspace_id', 'camera_id'], unique=False)
