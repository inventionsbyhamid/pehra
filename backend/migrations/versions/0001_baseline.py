"""baseline: the platform tables from packsure e97a509 and a society's cameras, watches, events, alerts,
incidents, gate ledger, staff register, device tokens and Telegram links, every one keyed by workspace_id

Revision ID: 0001
Revises:
Create Date: 2026-10-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table('audit',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=True),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('data', JSON, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit'))
    )
    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.create_index('ix_audit_action_ts', ['action', 'ts'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('billing_webhooks',
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('provider', 'id', name=op.f('pk_billing_webhooks'))
    )
    with op.batch_alter_table('billing_webhooks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_billing_webhooks_received_at'), ['received_at'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('payload', JSON, nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('error', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_status_run_after', ['status', 'run_after'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('workspaces',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('timezone', sa.String(), nullable=False),
    sa.Column('plan', sa.String(), nullable=False),
    sa.Column('subscription_status', sa.String(), nullable=True),
    sa.Column('polar_customer_id', sa.String(), nullable=True),
    sa.Column('grace_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('subscription_id', sa.String(), nullable=True),
    sa.Column('subscription_period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('subscription_ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('billing_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('comp_plan', sa.String(), nullable=True),
    sa.Column('comp_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('demo', sa.String(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces'))
    )
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_workspaces_demo'), ['demo'], unique=False)

    op.create_table('ai_calls',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('task', sa.String(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_ai_calls_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_calls'))
    )
    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.create_index('ix_ai_calls_ws_ts', ['workspace_id', 'ts'], unique=False)

    op.create_table('alerts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('watch_id', sa.String(), nullable=True),
    sa.Column('ts', sa.Float(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('summary', sa.String(), nullable=False),
    sa.Column('clip_key', sa.String(), nullable=True),
    sa.Column('keyframe_key', sa.String(), nullable=True),
    sa.Column('trace', JSON, nullable=False),
    sa.Column('feedback', sa.String(), nullable=True),
    sa.Column('fingerprint', sa.String(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_alerts_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts'))
    )
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.create_index('ix_alerts_ws_camera_ts', ['workspace_id', 'camera_id', 'ts'], unique=False)
        batch_op.create_index('ix_alerts_ws_fingerprint_ts', ['workspace_id', 'fingerprint', 'ts'], unique=False)
        batch_op.create_index('ix_alerts_ws_ts', ['workspace_id', 'ts'], unique=False)

    op.create_table('cameras',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('purpose', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('url_enc', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('last_frame_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('settings_json', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_cameras_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cameras'))
    )
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.create_index('ix_cameras_ws_created', ['workspace_id', 'created_at'], unique=False)

    op.create_table('events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('ts', sa.Float(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('cls', sa.String(), nullable=True),
    sa.Column('zone', sa.String(), nullable=True),
    sa.Column('data', JSON, nullable=False),
    sa.Column('keyframe_key', sa.String(), nullable=True),
    sa.Column('signature', JSON, nullable=True),
    sa.Column('embedding', JSON, nullable=True),
    sa.Column('embedded', sa.Boolean(), nullable=False),
    sa.Column('person_label', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_events_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_events'))
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index('ix_events_ws_camera_ts', ['workspace_id', 'camera_id', 'ts'], unique=False)
        batch_op.create_index('ix_events_ws_embedded_ts', ['workspace_id', 'embedded', 'ts'], unique=False)
        batch_op.create_index('ix_events_ws_ts', ['workspace_id', 'ts'], unique=False)

    op.create_table('incidents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=True),
    sa.Column('watch_id', sa.String(), nullable=True),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('keyframe_key', sa.String(), nullable=True),
    sa.Column('clip_key', sa.String(), nullable=True),
    sa.Column('ack_by', sa.String(), nullable=True),
    sa.Column('ack_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('telegram_message_id', sa.String(), nullable=True),
    sa.Column('agent_json', JSON, nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_incidents_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents'))
    )
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.create_index('ix_incidents_ws_camera_watch', ['workspace_id', 'camera_id', 'watch_id'], unique=False)
        batch_op.create_index('ix_incidents_ws_status_last', ['workspace_id', 'status', 'last_at'], unique=False)

    op.create_table('invites',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('used_by', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_invites_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invites'))
    )
    with op.batch_alter_table('invites', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invites_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('members',
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_members_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_members_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'user_id', name=op.f('pk_members'))
    )
    with op.batch_alter_table('members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_members_user_id'), ['user_id'], unique=False)

    op.create_table('meta',
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('value', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_meta_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'key', name=op.f('pk_meta'))
    )
    op.create_table('person_labels',
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('group_id', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_person_labels_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'camera_id', 'group_id', name=op.f('pk_person_labels'))
    )
    op.create_table('signin_links',
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_signin_links_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_signin_links'))
    )
    with op.batch_alter_table('signin_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_signin_links_user_id'), ['user_id'], unique=False)

    op.create_table('staff',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consent_by', sa.String(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_staff_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff'))
    )
    with op.batch_alter_table('staff', schema=None) as batch_op:
        batch_op.create_index('ix_staff_ws_active', ['workspace_id', 'active'], unique=False)

    op.create_table('telegram_links',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('chat_id', sa.String(), nullable=True),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('code_hash', sa.String(), nullable=True),
    sa.Column('code_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_telegram_links_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_links')),
    sa.UniqueConstraint('chat_id', name=op.f('uq_telegram_links_chat_id')),
    sa.UniqueConstraint('code_hash', name=op.f('uq_telegram_links_code_hash'))
    )
    with op.batch_alter_table('telegram_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_telegram_links_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('usage',
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('month', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_usage_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'month', 'name', name=op.f('pk_usage'))
    )
    op.create_table('user_identities',
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('subject', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_identities_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('provider', 'subject', name=op.f('pk_user_identities'))
    )
    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_identities_user_id'), ['user_id'], unique=False)

    op.create_table('vehicle_passages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('direction', sa.String(), nullable=False),
    sa.Column('cls', sa.String(), nullable=True),
    sa.Column('plate_text', sa.String(), nullable=True),
    sa.Column('plate_conf', sa.Float(), nullable=True),
    sa.Column('plate_colour', sa.String(), nullable=True),
    sa.Column('picture_key', sa.String(), nullable=True),
    sa.Column('paired_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_vehicle_passages_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vehicle_passages'))
    )
    with op.batch_alter_table('vehicle_passages', schema=None) as batch_op:
        batch_op.create_index('ix_vehicle_passages_ws_at', ['workspace_id', 'at'], unique=False)
        batch_op.create_index('ix_vehicle_passages_ws_plate', ['workspace_id', 'plate_text'], unique=False)

    op.create_table('watches',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('scope', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=True),
    sa.Column('excluded_cameras_json', JSON, nullable=False),
    sa.Column('text', sa.String(), nullable=False),
    sa.Column('spec_json', JSON, nullable=False),
    sa.Column('output', sa.String(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('coverage_json', JSON, nullable=False),
    sa.Column('setup_json', JSON, nullable=False),
    sa.Column('cannot_json', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_watches_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watches'))
    )
    with op.batch_alter_table('watches', schema=None) as batch_op:
        batch_op.create_index('ix_watches_ws_camera', ['workspace_id', 'camera_id'], unique=False)

    op.create_table('attendance',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('staff_id', sa.String(), nullable=False),
    sa.Column('day', sa.String(), nullable=False),
    sa.Column('first_in', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_out', sa.DateTime(timezone=True), nullable=True),
    sa.Column('camera_ids_json', JSON, nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_attendance_staff_id_staff'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_attendance_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance')),
    sa.UniqueConstraint('workspace_id', 'staff_id', 'day', name='uq_attendance_ws_staff_day')
    )
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.create_index('ix_attendance_ws_day', ['workspace_id', 'day'], unique=False)

    op.create_table('device_tokens',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], name=op.f('fk_device_tokens_camera_id_cameras'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_device_tokens_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_device_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_device_tokens_token_hash'))
    )
    with op.batch_alter_table('device_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_device_tokens_camera_id'), ['camera_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_device_tokens_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('staff_faces',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('staff_id', sa.String(), nullable=False),
    sa.Column('embedding', sa.LargeBinary(), nullable=False),
    sa.Column('picture_key', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_faces_staff_id_staff'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_staff_faces_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_faces'))
    )
    with op.batch_alter_table('staff_faces', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_staff_faces_staff_id'), ['staff_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_staff_faces_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('zones',
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('polygon_json', JSON, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], name=op.f('fk_zones_camera_id_cameras'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_zones_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('camera_id', 'id', name=op.f('pk_zones'))
    )
    with op.batch_alter_table('zones', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_zones_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('state_examples',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workspace_id', sa.String(), nullable=False),
    sa.Column('camera_id', sa.String(), nullable=False),
    sa.Column('zone_id', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('picture_key', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['camera_id', 'zone_id'], ['zones.camera_id', 'zones.id'], name='fk_state_examples_zone_zones', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], name=op.f('fk_state_examples_camera_id_cameras'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_state_examples_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_state_examples'))
    )
    with op.batch_alter_table('state_examples', schema=None) as batch_op:
        batch_op.create_index('ix_state_examples_ws_camera', ['workspace_id', 'camera_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('state_examples', schema=None) as batch_op:
        batch_op.drop_index('ix_state_examples_ws_camera')

    op.drop_table('state_examples')
    with op.batch_alter_table('zones', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_zones_workspace_id'))

    op.drop_table('zones')
    with op.batch_alter_table('staff_faces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_staff_faces_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_staff_faces_staff_id'))

    op.drop_table('staff_faces')
    with op.batch_alter_table('device_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_device_tokens_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_device_tokens_camera_id'))

    op.drop_table('device_tokens')
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.drop_index('ix_attendance_ws_day')

    op.drop_table('attendance')
    with op.batch_alter_table('watches', schema=None) as batch_op:
        batch_op.drop_index('ix_watches_ws_camera')

    op.drop_table('watches')
    with op.batch_alter_table('vehicle_passages', schema=None) as batch_op:
        batch_op.drop_index('ix_vehicle_passages_ws_plate')
        batch_op.drop_index('ix_vehicle_passages_ws_at')

    op.drop_table('vehicle_passages')
    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_identities_user_id'))

    op.drop_table('user_identities')
    op.drop_table('usage')
    with op.batch_alter_table('telegram_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_telegram_links_workspace_id'))

    op.drop_table('telegram_links')
    with op.batch_alter_table('staff', schema=None) as batch_op:
        batch_op.drop_index('ix_staff_ws_active')

    op.drop_table('staff')
    with op.batch_alter_table('signin_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_signin_links_user_id'))

    op.drop_table('signin_links')
    op.drop_table('person_labels')
    op.drop_table('meta')
    with op.batch_alter_table('members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_members_user_id'))

    op.drop_table('members')
    with op.batch_alter_table('invites', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invites_workspace_id'))

    op.drop_table('invites')
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index('ix_incidents_ws_status_last')
        batch_op.drop_index('ix_incidents_ws_camera_watch')

    op.drop_table('incidents')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index('ix_events_ws_ts')
        batch_op.drop_index('ix_events_ws_embedded_ts')
        batch_op.drop_index('ix_events_ws_camera_ts')

    op.drop_table('events')
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.drop_index('ix_cameras_ws_created')

    op.drop_table('cameras')
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_index('ix_alerts_ws_ts')
        batch_op.drop_index('ix_alerts_ws_fingerprint_ts')
        batch_op.drop_index('ix_alerts_ws_camera_ts')

    op.drop_table('alerts')
    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_calls_ws_ts')

    op.drop_table('ai_calls')
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_workspaces_demo'))

    op.drop_table('workspaces')
    op.drop_table('users')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_status_run_after')

    op.drop_table('jobs')
    with op.batch_alter_table('billing_webhooks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_billing_webhooks_received_at'))

    op.drop_table('billing_webhooks')
    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_workspace_id'))
        batch_op.drop_index('ix_audit_action_ts')

    op.drop_table('audit')
