"""Tables. Ids are short random strings with a prefix per table, safe in URLs, and exist from construction.
Platform times are aware UTC datetimes; the engine's events and alerts keep float epoch seconds (`ts`), as the
Repository dataclasses do. Every table of a society's data has a workspace_id; tokens and codes are stored only as
hashes. The schema itself comes from the Alembic revisions in backend/migrations; change both together."""
import secrets
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import (JSON, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, LargeBinary, MetaData, String,
                        UniqueConstraint, event, types)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_ID_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def new_id(prefix: str) -> str:
    return f"{prefix}_{''.join(secrets.choice(_ID_CHARS) for _ in range(16))}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def pack_vec(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_vec(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


class UTCDateTime(types.TypeDecorator):
    """Aware datetimes in, aware UTC datetimes out; SQLite keeps naive UTC text. Naive input is a bug."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime {value!r}; pass an aware one")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def json_type(none_as_null: bool = False):
    return JSON(none_as_null=none_as_null).with_variant(JSONB(none_as_null=none_as_null), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })
    type_annotation_map = {
        datetime: UTCDateTime(),
        float: Float(),
        bytes: LargeBinary(),
        list[str]: MutableList.as_mutable(json_type()),
        list[Any]: MutableList.as_mutable(json_type()),
        dict[str, Any]: MutableDict.as_mutable(json_type()),
    }


def id_column(prefix: str):
    return mapped_column(String, primary_key=True, default=lambda: new_id(prefix), info={"prefix": prefix})


@event.listens_for(Base, "init", propagate=True)
def _give_id(target, args, kwargs):
    """A new row's id exists before it is saved, so related rows can point at it in the same transaction."""
    column = target.__table__.c.get("id")
    if column is not None and "prefix" in column.info and kwargs.get("id") is None:
        kwargs["id"] = new_id(column.info["prefix"])


def workspace_fk(**kw):
    return mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), **kw)


# platform (from packsure e97a509)

class Workspace(Base):
    """A society."""
    __tablename__ = "workspaces"
    id: Mapped[str] = id_column("ws")
    name: Mapped[str]
    timezone: Mapped[str] = mapped_column(default="Asia/Kolkata")
    plan: Mapped[str] = mapped_column(default="free")
    subscription_status: Mapped[str | None]
    polar_customer_id: Mapped[str | None]  # the billing provider's customer id
    grace_until: Mapped[datetime | None]
    subscription_id: Mapped[str | None]  # the provider's, for the subscription last applied
    subscription_period_end: Mapped[datetime | None]
    subscription_ends_at: Mapped[datetime | None]  # set while cancelled for the end of the period
    billing_synced_at: Mapped[datetime | None]  # when the provider last changed the subscription last applied
    comp_plan: Mapped[str | None]  # a plan given by the operator, not a subscription
    comp_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    demo: Mapped[str | None] = mapped_column(index=True)  # sandbox (one demo visitor's) or showcase (read only)
    expires_at: Mapped[datetime | None]  # a sandbox is deleted by the daily demo reset after this


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = id_column("usr")
    email: Mapped[str | None] = mapped_column(unique=True)  # None only for a demo visitor
    name: Mapped[str | None]
    demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserIdentity(Base):
    """A person's id at one sign in provider (Google's sub, ...); one user may have several."""
    __tablename__ = "user_identities"
    provider: Mapped[str] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Member(Base):
    """A user's role in a society: viewer, supervisor, admin or owner."""
    __tablename__ = "members"
    workspace_id: Mapped[str] = workspace_fk(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(default="viewer")


class SignInLink(Base):
    """A single use sign in link the operator made on the server; only its token's SHA 256."""
    __tablename__ = "signin_links"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str | None]  # the society it opens; None: the user's oldest
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class Invite(Base):
    """A link that adds someone to a society. The link is signed; this row makes it single use and revocable."""
    __tablename__ = "invites"
    id: Mapped[str] = id_column("inv")
    workspace_id: Mapped[str] = workspace_fk(index=True)
    role: Mapped[str] = mapped_column(default="supervisor")
    created_by: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by: Mapped[str | None]


class Job(Base):
    """A background job. While running, run_after is the lease: past it, another worker may take the job."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)
    id: Mapped[str] = id_column("job")
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(default="queued")  # queued, running, done, failed
    attempts: Mapped[int] = mapped_column(default=0)
    run_after: Mapped[datetime] = mapped_column(default=utcnow)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Usage(Base):
    """A count per society, month and name, such as AI checks; months follow the society's time zone."""
    __tablename__ = "usage"
    workspace_id: Mapped[str] = workspace_fk(primary_key=True)
    month: Mapped[str] = mapped_column(primary_key=True)  # "2026-10"
    name: Mapped[str] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(default=0)


class BillingWebhook(Base):
    """A billing webhook delivery already handled, so a retry is dropped. Kept without a foreign key, like Audit."""
    __tablename__ = "billing_webhooks"
    provider: Mapped[str] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)  # the delivery's id (webhook-id)
    type: Mapped[str]
    workspace_id: Mapped[str | None]
    received_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Audit(Base):
    """Who did what. Kept when a society is deleted, so no foreign key."""
    __tablename__ = "audit"
    __table_args__ = (Index("ix_audit_action_ts", "action", "ts"),)
    id: Mapped[str] = id_column("aud")
    workspace_id: Mapped[str | None] = mapped_column(index=True)
    actor: Mapped[str]
    action: Mapped[str]
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ts: Mapped[datetime] = mapped_column(default=utcnow)


class AiCall(Base):
    """One AI request, counted against the society's daily limit (AI_DAILY_CALL_LIMIT)."""
    __tablename__ = "ai_calls"
    __table_args__ = (Index("ix_ai_calls_ws_ts", "workspace_id", "ts"),)
    id: Mapped[str] = id_column("ai")
    workspace_id: Mapped[str] = workspace_fk()
    task: Mapped[str]
    ts: Mapped[datetime] = mapped_column(default=utcnow)


# a society's cameras and what they see

class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (Index("ix_cameras_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = id_column("cam")
    workspace_id: Mapped[str] = workspace_fk()
    name: Mapped[str]
    purpose: Mapped[str] = mapped_column(default="other")  # an id from data/purposes.json
    kind: Mapped[str]  # rtsp (pulled), push (a browser, phone or bridge sends frames) or file
    url_enc: Mapped[str | None]  # the stream address, encrypted (store/sql.py UrlBox)
    status: Mapped[str] = mapped_column(default="offline")  # live, offline, covered, moved or blurred
    last_frame_at: Mapped[datetime | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    settings_json: Mapped[dict[str, Any]] = mapped_column(default=dict)  # tuning overrides, last scan, loop
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Zone(Base):
    """An area, a water area or a line drawn on a camera; its id is unique within the camera."""
    __tablename__ = "zones"
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = workspace_fk(index=True)
    name: Mapped[str]
    polygon_json: Mapped[list[Any]] = mapped_column(default=list)  # normalized 0..1 points
    kind: Mapped[str] = mapped_column(default="area")  # area, water or line
    position: Mapped[int] = mapped_column(default=0)


class StateExample(Base):
    """A saved picture of a zone in a named state (gate open, gate closed), for the zone_state signal."""
    __tablename__ = "state_examples"
    __table_args__ = (ForeignKeyConstraint(["camera_id", "zone_id"], ["zones.camera_id", "zones.id"],
                                           name="fk_state_examples_zone_zones", ondelete="CASCADE"),
                      Index("ix_state_examples_ws_camera", "workspace_id", "camera_id"))
    id: Mapped[str] = id_column("stx")
    workspace_id: Mapped[str] = workspace_fk()
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    zone_id: Mapped[str]
    state: Mapped[str]
    picture_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Watch(Base):
    """A watch typed in plain words: on one camera, or site wide (every camera not excluded)."""
    __tablename__ = "watches"
    __table_args__ = (Index("ix_watches_ws_camera", "workspace_id", "camera_id"),)
    id: Mapped[str] = id_column("wat")
    workspace_id: Mapped[str] = workspace_fk()
    scope: Mapped[str] = mapped_column(default="camera")  # camera or site
    camera_id: Mapped[str | None]  # None for a site watch
    excluded_cameras_json: Mapped[list[str]] = mapped_column(default=list)
    text: Mapped[str] = mapped_column(default="")
    spec_json: Mapped[dict[str, Any]] = mapped_column(default=dict)  # the compiled rules.watch.Watch
    output: Mapped[str] = mapped_column(default="alert")  # alert or log
    severity: Mapped[str] = mapped_column(default="info")  # critical, warning or info
    enabled: Mapped[bool] = mapped_column(default=True)
    coverage_json: Mapped[list[Any]] = mapped_column(default=list)
    setup_json: Mapped[list[Any]] = mapped_column(default=list)
    cannot_json: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Event(Base):
    """Something a camera saw. No foreign key to the camera, so the timeline outlives it until retention."""
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_ws_camera_ts", "workspace_id", "camera_id", "ts"),
                      Index("ix_events_ws_ts", "workspace_id", "ts"),
                      Index("ix_events_ws_embedded_ts", "workspace_id", "embedded", "ts"))
    id: Mapped[str] = id_column("evt")
    workspace_id: Mapped[str] = workspace_fk()
    camera_id: Mapped[str]
    ts: Mapped[float]
    kind: Mapped[str]
    track_id: Mapped[int | None]
    cls: Mapped[str | None]
    zone: Mapped[str | None]
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    keyframe_key: Mapped[str | None]
    signature: Mapped[list[Any] | None] = mapped_column(json_type(none_as_null=True))
    embedding: Mapped[list[Any] | None] = mapped_column(json_type(none_as_null=True))
    embedded: Mapped[bool] = mapped_column(default=False)  # the search index has this moment
    person_label: Mapped[str | None]


class Alert(Base):
    """One checked moment from a watch, with the agent's trace; Incident groups repeats."""
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_ws_ts", "workspace_id", "ts"),
                      Index("ix_alerts_ws_camera_ts", "workspace_id", "camera_id", "ts"),
                      Index("ix_alerts_ws_fingerprint_ts", "workspace_id", "fingerprint", "ts"))
    id: Mapped[str] = id_column("alt")
    workspace_id: Mapped[str] = workspace_fk()
    camera_id: Mapped[str]
    watch_id: Mapped[str | None]
    ts: Mapped[float]
    severity: Mapped[str]
    status: Mapped[str]  # alert, unverified, dismissed, awaiting_human, approved, declined
    summary: Mapped[str] = mapped_column(default="")
    clip_key: Mapped[str | None]
    keyframe_key: Mapped[str | None]
    trace: Mapped[list[Any]] = mapped_column(default=list)
    feedback: Mapped[str | None]  # up or down
    fingerprint: Mapped[str]
    track_id: Mapped[int | None]


class Incident(Base):
    """Repeats of one situation on one camera, grouped: one Telegram message, edited as it changes."""
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_ws_status_last", "workspace_id", "status", "last_at"),
                      Index("ix_incidents_ws_camera_watch", "workspace_id", "camera_id", "watch_id"))
    id: Mapped[str] = id_column("inc")
    workspace_id: Mapped[str] = workspace_fk()
    camera_id: Mapped[str | None]
    watch_id: Mapped[str | None]
    severity: Mapped[str]  # critical, warning or info
    status: Mapped[str] = mapped_column(default="open")  # open, acknowledged, false_alarm or resolved
    opened_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_at: Mapped[datetime] = mapped_column(default=utcnow)
    count: Mapped[int] = mapped_column(default=1)
    reason: Mapped[str] = mapped_column(default="")
    keyframe_key: Mapped[str | None]
    clip_key: Mapped[str | None]
    ack_by: Mapped[str | None]
    ack_at: Mapped[datetime | None]
    telegram_message_id: Mapped[str | None]
    agent_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class VehiclePassage(Base):
    """A vehicle through a gate line, for the gate ledger; paired_id links an in with its out."""
    __tablename__ = "vehicle_passages"
    __table_args__ = (Index("ix_vehicle_passages_ws_at", "workspace_id", "at"),
                      Index("ix_vehicle_passages_ws_plate", "workspace_id", "plate_text"))
    id: Mapped[str] = id_column("veh")
    workspace_id: Mapped[str] = workspace_fk()
    camera_id: Mapped[str]
    at: Mapped[datetime]
    direction: Mapped[str]  # in or out
    cls: Mapped[str | None]
    plate_text: Mapped[str | None]
    plate_conf: Mapped[float | None]
    plate_colour: Mapped[str | None]  # yellow, white, green, black or unknown
    picture_key: Mapped[str | None]
    paired_id: Mapped[str | None]


class Staff(Base):
    """A consenting adult staff member (maid, driver, cook, guard). Kept, inactive, when their faces are deleted."""
    __tablename__ = "staff"
    __table_args__ = (Index("ix_staff_ws_active", "workspace_id", "active"),)
    id: Mapped[str] = id_column("stf")
    workspace_id: Mapped[str] = workspace_fk()
    name: Mapped[str]
    role: Mapped[str] = mapped_column(default="staff")
    consent_at: Mapped[datetime | None]
    consent_by: Mapped[str | None]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class StaffFace(Base):
    """A face template (float32 vector) of a staff member; never a face from anyone who did not consent."""
    __tablename__ = "staff_faces"
    id: Mapped[str] = id_column("sfc")
    workspace_id: Mapped[str] = workspace_fk(index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    embedding: Mapped[bytes]
    picture_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Attendance(Base):
    """First in and last out of one staff member on one day (the society's time zone)."""
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("workspace_id", "staff_id", "day", name="uq_attendance_ws_staff_day"),
                      Index("ix_attendance_ws_day", "workspace_id", "day"))
    id: Mapped[str] = id_column("att")
    workspace_id: Mapped[str] = workspace_fk()
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    day: Mapped[str]  # "2026-10-25"
    first_in: Mapped[datetime | None]
    last_out: Mapped[datetime | None]
    camera_ids_json: Mapped[list[str]] = mapped_column(default=list)


class DeviceToken(Base):
    """Lets a browser, phone or bridge push frames for one camera; only the token's hash is kept."""
    __tablename__ = "device_tokens"
    id: Mapped[str] = id_column("dev")
    workspace_id: Mapped[str] = workspace_fk(index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rotated_at: Mapped[datetime | None]


class TelegramLink(Base):
    """A society's Telegram group: a one time code (hashed) until the group sends it, then the chat id."""
    __tablename__ = "telegram_links"
    id: Mapped[str] = id_column("tg")
    workspace_id: Mapped[str] = workspace_fk(index=True)
    chat_id: Mapped[str | None] = mapped_column(unique=True)
    title: Mapped[str | None]
    connected_at: Mapped[datetime | None]
    code_hash: Mapped[str | None] = mapped_column(unique=True)
    code_expires_at: Mapped[datetime | None]


class PersonLabel(Base):
    """A name a supervisor gave to a group of sightings on one camera."""
    __tablename__ = "person_labels"
    workspace_id: Mapped[str] = workspace_fk(primary_key=True)
    camera_id: Mapped[str] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]


class Meta(Base):
    """Small values per society, such as the embedding model its search index was built with."""
    __tablename__ = "meta"
    workspace_id: Mapped[str] = workspace_fk(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
