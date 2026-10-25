"""The Repository on SQLAlchemy. One SqlRepository is bound to one workspace (a society): every query filters by its
workspace_id, so an id from another society is simply not found, and a write to one changes nothing."""
import base64
import hashlib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, delete, func, insert, or_, orm, select, update

from watchful.db import models as m
from watchful.registry import register
from watchful.store.db import DEFAULT_WORKSPACE, AlertRow, Camera, EventRow, WatchRow
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone

log = logging.getLogger("watchful.store")

DEFAULT_NAME = "Demo society"
KIND_TO_ROW = {"stream": "rtsp", "webcam": "push", "file": "file", "demo": "file"}
ROW_TO_KIND = {"rtsp": "stream", "push": "webcam", "file": "file"}
ZONE_FIELDS = {f.name for f in fields(Zone)}
IN_BATCH = 500


def _dt(ts: float | None) -> datetime | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC)


def _epoch(dt: datetime | None) -> float | None:
    return None if dt is None else dt.timestamp()


class UrlBox:
    """Camera addresses carry NVR passwords, so they are stored encrypted with a key derived from the secret.
    Without a secret (tests and tools on a scratch database) they are stored as given, marked plain."""

    def __init__(self, secret: str | None):
        key = hashlib.sha256(b"camera-url:" + secret.encode()).digest() if secret else None
        self._fernet = Fernet(base64.urlsafe_b64encode(key)) if key else None

    def seal(self, url: str | None) -> str | None:
        if url is None:
            return None
        if self._fernet is None:
            return "plain:" + url
        return "f1:" + self._fernet.encrypt(url.encode()).decode()

    def open(self, value: str | None) -> str | None:
        if value is None:
            return None
        scheme, _, body = value.partition(":")
        if scheme == "plain":
            return body
        try:
            return self._fernet.decrypt(body.encode()).decode() if self._fernet else None
        except InvalidToken:
            log.warning("a camera address was stored with another key (DATA_KEY changed?)")
            return None


def create_workspace(engine: Engine, name: str, *, id: str | None = None, timezone: str = "Asia/Kolkata",
                     **extra) -> m.Workspace:
    with orm.Session(engine, expire_on_commit=False) as s, s.begin():
        ws = m.Workspace(id=id, name=name, timezone=timezone, **extra)
        s.add(ws)
    return ws


def ensure_workspace(engine: Engine, workspace_id: str, name: str = DEFAULT_NAME) -> None:
    with orm.Session(engine) as s:
        if s.get(m.Workspace, workspace_id) is not None:
            return
    create_workspace(engine, name, id=workspace_id)


def open_sqlite(path: str | Path, workspace_id: str = DEFAULT_WORKSPACE, secret: str | None = None):
    from watchful.db.session import make_engine, migrate
    engine = make_engine(f"sqlite:///{path}")
    migrate(engine)
    if workspace_id == DEFAULT_WORKSPACE:
        ensure_workspace(engine, DEFAULT_WORKSPACE)
    return SqlRepository(engine, workspace_id, secret=secret)


def _zone(z: m.Zone) -> Zone:
    extra = {"kind": z.kind} if "kind" in ZONE_FIELDS else {}
    return Zone(z.id, z.name, [tuple(p) for p in z.polygon_json or []], **extra)


def _event(r: m.Event) -> EventRow:
    return EventRow(r.id, r.camera_id, r.ts, r.kind, r.track_id, r.cls, r.zone, dict(r.data or {}), r.keyframe_key,
                    r.signature, r.embedding, r.person_label, r.workspace_id)


def _watch(r: m.Watch) -> WatchRow:
    return WatchRow(r.id, r.camera_id, r.text, dict(r.spec_json or {}), r.enabled, r.workspace_id, r.scope,
                    list(r.excluded_cameras_json or []), r.output, r.severity, list(r.coverage_json or []),
                    list(r.setup_json or []), list(r.cannot_json or []))


def _watch_columns(row: WatchRow) -> dict:
    spec = row.watch or {}
    return {"scope": spec.get("scope", row.scope), "text": row.text, "spec_json": spec,
            "excluded_cameras_json": list(spec.get("excluded_cameras", row.excluded_cameras) or []),
            "output": spec.get("output", row.output), "severity": spec.get("severity", row.severity),
            "enabled": row.enabled, "coverage_json": list(row.coverage), "setup_json": list(row.setup),
            "cannot_json": list(row.cannot)}


def _alert(r: m.Alert) -> AlertRow:
    return AlertRow(r.id, r.camera_id, r.watch_id, r.ts, r.severity, r.status, r.summary, r.clip_key,
                    r.keyframe_key, list(r.trace or []), r.feedback, r.fingerprint, r.track_id, r.workspace_id)


def _alert_columns(row: AlertRow) -> dict:
    return {"watch_id": row.watch_id, "ts": row.ts, "severity": row.severity, "status": row.status,
            "summary": row.summary, "clip_key": row.clip_key, "keyframe_key": row.keyframe_key,
            "trace": list(row.trace or []), "feedback": row.feedback, "fingerprint": row.fingerprint,
            "track_id": row.track_id}


class SqlRepository:
    def __init__(self, engine: Engine, workspace_id: str, secret: str | None = None):
        self.engine, self.workspace_id = engine, workspace_id
        self._sessions = orm.sessionmaker(engine, expire_on_commit=False)
        self._box = UrlBox(secret)

    @contextmanager
    def tx(self) -> Iterator[orm.Session]:
        with self._sessions.begin() as s:
            yield s

    def _mine(self, model):
        return model.workspace_id == self.workspace_id

    # cameras

    def _camera(self, row: m.Camera, zones: list[m.Zone]) -> Camera:
        st = row.settings_json or {}
        kind = "demo" if row.kind == "file" and st.get("loop") else ROW_TO_KIND.get(row.kind, row.kind)
        return Camera(row.id, row.name, kind, self._box.open(row.url_enc), [_zone(z) for z in zones], row.enabled,
                      dict(st.get("tuning") or {}), st.get("last_scan"), row.workspace_id, row.purpose, row.status,
                      _epoch(row.last_frame_at))

    def _write_camera(self, s: orm.Session, row: m.Camera, cam: Camera) -> None:
        row.name, row.purpose, row.kind = cam.name, cam.purpose or "other", KIND_TO_ROW.get(cam.kind, cam.kind)
        row.url_enc, row.status, row.enabled = self._box.seal(cam.url), cam.status, cam.enabled
        row.last_frame_at = _dt(cam.last_frame_at)
        row.settings_json = {"tuning": cam.tuning or {}, "last_scan": cam.last_scan, "loop": cam.kind == "demo"}
        have = {z.id: z for z in s.scalars(select(m.Zone).where(m.Zone.camera_id == row.id, self._mine(m.Zone)))}
        keep = set()
        for position, z in enumerate(cam.zones):
            zr = have.get(z.id)
            if zr is None:
                zr = have[z.id] = m.Zone(camera_id=row.id, id=z.id, workspace_id=self.workspace_id)
                s.add(zr)
            zr.name, zr.polygon_json, zr.position = z.name, [list(p) for p in z.polygon], position
            zr.kind = getattr(z, "kind", "area")
            keep.add(z.id)
        for zone_id, zr in have.items():
            if zone_id not in keep:
                s.delete(zr)

    def _zones(self, s: orm.Session, camera_ids: list[str]) -> dict[str, list[m.Zone]]:
        out: dict[str, list[m.Zone]] = {cid: [] for cid in camera_ids}
        for z in s.scalars(select(m.Zone).where(self._mine(m.Zone), m.Zone.camera_id.in_(camera_ids))
                           .order_by(m.Zone.position)):
            out[z.camera_id].append(z)
        return out

    def add_camera(self, cam: Camera) -> Camera:
        cam.id, cam.workspace_id = cam.id or m.new_id("cam"), self.workspace_id
        with self.tx() as s:
            row = m.Camera(id=cam.id, workspace_id=self.workspace_id, name=cam.name,
                           kind=KIND_TO_ROW.get(cam.kind, cam.kind))
            s.add(row)
            s.flush()
            self._write_camera(s, row, cam)
        return cam

    def get_camera(self, camera_id: str) -> Camera | None:
        with self.tx() as s:
            row = s.scalar(select(m.Camera).where(m.Camera.id == camera_id, self._mine(m.Camera)))
            return self._camera(row, self._zones(s, [row.id])[row.id]) if row else None

    def list_cameras(self) -> list[Camera]:
        with self.tx() as s:
            rows = list(s.scalars(select(m.Camera).where(self._mine(m.Camera))
                                  .order_by(m.Camera.created_at, m.Camera.id)))
            zones = self._zones(s, [r.id for r in rows])
            return [self._camera(r, zones[r.id]) for r in rows]

    def update_camera(self, cam: Camera) -> None:
        with self.tx() as s:
            row = s.scalar(select(m.Camera).where(m.Camera.id == cam.id, self._mine(m.Camera)))
            if row is not None:
                self._write_camera(s, row, cam)

    def delete_camera(self, camera_id: str) -> None:
        with self.tx() as s:
            if s.scalar(select(m.Camera.id).where(m.Camera.id == camera_id, self._mine(m.Camera))) is None:
                return
            for model in (m.Event, m.Alert, m.PersonLabel, m.Zone):
                s.execute(delete(model).where(self._mine(model), model.camera_id == camera_id))
            s.execute(delete(m.Watch).where(self._mine(m.Watch), m.Watch.camera_id == camera_id,
                                            m.Watch.scope == "camera"))
            s.execute(delete(m.Camera).where(self._mine(m.Camera), m.Camera.id == camera_id))

    def delete_camera_events(self, camera_id: str) -> int:
        with self.tx() as s:
            return s.execute(delete(m.Event).where(self._mine(m.Event), m.Event.camera_id == camera_id)).rowcount

    # events

    def add_events(self, rows: list[EventRow]) -> None:
        if not rows:
            return
        values = []
        for r in rows:
            r.id, r.workspace_id = r.id or m.new_id("evt"), self.workspace_id
            values.append({"id": r.id, "workspace_id": self.workspace_id, "camera_id": r.camera_id, "ts": r.ts,
                           "kind": r.kind, "track_id": r.track_id, "cls": r.cls, "zone": r.zone,
                           "data": r.data or {}, "keyframe_key": r.keyframe_key, "signature": r.signature,
                           "embedding": r.embedding, "embedded": r.embedding is not None,
                           "person_label": r.person_label})
        with self.tx() as s:
            for start in range(0, len(values), IN_BATCH):
                s.execute(insert(m.Event), values[start:start + IN_BATCH])

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None,
                     limit=500) -> list[EventRow]:
        stmt = select(m.Event).where(self._mine(m.Event))
        for column, value in ((m.Event.camera_id, camera_id), (m.Event.zone, zone), (m.Event.cls, cls)):
            if value is not None:
                stmt = stmt.where(column == value)
        if kinds:
            stmt = stmt.where(m.Event.kind.in_(kinds))
        if since is not None:
            stmt = stmt.where(m.Event.ts >= since)
        if until is not None:
            stmt = stmt.where(m.Event.ts <= until)
        with self.tx() as s:  # the newest rows, returned oldest first
            rows = list(s.scalars(stmt.order_by(m.Event.ts.desc()).limit(limit)))
        return [_event(r) for r in reversed(rows)]

    def get_event(self, event_id: str) -> EventRow | None:
        with self.tx() as s:
            row = s.scalar(select(m.Event).where(m.Event.id == event_id, self._mine(m.Event)))
            return _event(row) if row else None

    def events_missing_embedding(self, kinds: list[str], limit: int = 64) -> list[EventRow]:
        with self.tx() as s:
            rows = s.scalars(select(m.Event).where(self._mine(m.Event), m.Event.embedded.is_(False),
                                                   m.Event.kind.in_(kinds))
                             .order_by(m.Event.ts.desc()).limit(limit))
            return [_event(r) for r in rows]

    def clear_embeddings(self) -> int:
        with self.tx() as s:
            return s.execute(update(m.Event).where(self._mine(m.Event), m.Event.embedded.is_(True))
                             .values(embedding=None, embedded=False)).rowcount

    def set_event_embedding(self, event_id: str, embedding: list[float]) -> None:
        with self.tx() as s:
            s.execute(update(m.Event).where(m.Event.id == event_id, self._mine(m.Event))
                      .values(embedding=[round(float(x), 5) for x in embedding], embedded=True))

    def delete_events_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        with self.tx() as s:
            return s.execute(delete(m.Event).where(self._mine(m.Event), m.Event.ts < ts,
                                                   m.Event.camera_id.not_in(list(keep_cameras)))).rowcount

    def delete_alerts_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        with self.tx() as s:
            return s.execute(delete(m.Alert).where(self._mine(m.Alert), m.Alert.ts < ts,
                                                   m.Alert.camera_id.not_in(list(keep_cameras)))).rowcount

    # meta

    def get_meta(self, key: str) -> str | None:
        with self.tx() as s:
            row = s.get(m.Meta, (self.workspace_id, key))
            return row.value if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as s:
            row = s.get(m.Meta, (self.workspace_id, key))
            if row is None:
                s.add(m.Meta(workspace_id=self.workspace_id, key=key, value=value))
            else:
                row.value = value

    # watches

    def add_watch(self, row: WatchRow) -> WatchRow:
        row.id, row.workspace_id = row.id or m.new_id("wat"), self.workspace_id
        with self.tx() as s:
            s.add(m.Watch(id=row.id, workspace_id=self.workspace_id, camera_id=row.camera_id, **_watch_columns(row)))
        return _as_stored(row)

    def list_watches(self, camera_id=None) -> list[WatchRow]:
        """A camera's watches are its own and the site wide watches that do not exclude it."""
        stmt = select(m.Watch).where(self._mine(m.Watch))
        if camera_id is not None:
            stmt = stmt.where(or_(m.Watch.camera_id == camera_id, m.Watch.scope == "site"))
        with self.tx() as s:
            rows = list(s.scalars(stmt.order_by(m.Watch.created_at, m.Watch.id)))
        return [_watch(r) for r in rows
                if camera_id is None or r.scope != "site" or camera_id not in (r.excluded_cameras_json or [])]

    def update_watch(self, row: WatchRow) -> None:
        with self.tx() as s:
            s.execute(update(m.Watch).where(m.Watch.id == row.id, self._mine(m.Watch)).values(**_watch_columns(row)))

    # alerts

    def add_alert(self, row: AlertRow) -> AlertRow:
        row.id, row.workspace_id = row.id or m.new_id("alt"), self.workspace_id
        with self.tx() as s:
            s.add(m.Alert(id=row.id, workspace_id=self.workspace_id, camera_id=row.camera_id, **_alert_columns(row)))
        return row

    def get_alert(self, alert_id: str) -> AlertRow | None:
        with self.tx() as s:
            row = s.scalar(select(m.Alert).where(m.Alert.id == alert_id, self._mine(m.Alert)))
            return _alert(row) if row else None

    def update_alert(self, row: AlertRow) -> None:
        with self.tx() as s:
            s.execute(update(m.Alert).where(m.Alert.id == row.id, self._mine(m.Alert)).values(**_alert_columns(row)))

    def list_alerts(self, camera_id=None, limit=100) -> list[AlertRow]:
        stmt = select(m.Alert).where(self._mine(m.Alert))
        if camera_id is not None:
            stmt = stmt.where(m.Alert.camera_id == camera_id)
        with self.tx() as s:
            return [_alert(r) for r in s.scalars(stmt.order_by(m.Alert.ts.desc()).limit(limit))]

    def alert_fingerprint_exists(self, fp: str, within_s: float) -> bool:
        with self.tx() as s:
            return s.scalar(select(m.Alert.id).where(self._mine(m.Alert), m.Alert.fingerprint == fp,
                                                     m.Alert.ts >= time.time() - within_s).limit(1)) is not None

    # people: an enrolment is an active staff member and their face template

    def add_enrollment(self, e: Enrollment) -> None:
        e.workspace_id = self.workspace_id
        with self.tx() as s:
            staff = s.scalar(select(m.Staff).where(m.Staff.id == e.person_id, self._mine(m.Staff)))
            if staff is None:
                staff = m.Staff(id=e.person_id, workspace_id=self.workspace_id, name=e.name)
                s.add(staff)
                s.flush()  # another society's id fails here, before anything of theirs is touched
            staff.name, staff.active, staff.consent_at = e.name, True, _dt(e.consent_ts)
            s.execute(delete(m.StaffFace).where(m.StaffFace.staff_id == staff.id, self._mine(m.StaffFace)))
            s.add(m.StaffFace(workspace_id=self.workspace_id, staff_id=staff.id, embedding=m.pack_vec(e.feature),
                              picture_key=e.thumbnail_key))

    def list_enrollments(self) -> list[Enrollment]:
        with self.tx() as s:
            rows = s.execute(select(m.Staff, m.StaffFace).join(m.StaffFace, m.StaffFace.staff_id == m.Staff.id)
                             .where(self._mine(m.Staff), m.Staff.active.is_(True))
                             .order_by(m.Staff.created_at, m.Staff.id, m.StaffFace.created_at)).all()
        seen, out = set(), []
        for staff, face in rows:
            if staff.id in seen:
                continue
            seen.add(staff.id)
            consent = staff.consent_at or staff.created_at
            out.append(Enrollment(staff.id, staff.name, m.unpack_vec(face.embedding).tolist(), consent.timestamp(),
                                  face.picture_key, staff.workspace_id))
        return out

    def delete_enrollment(self, person_id: str) -> None:
        """The face templates go; the staff member stays, inactive, so their attendance history is kept."""
        with self.tx() as s:
            s.execute(delete(m.StaffFace).where(m.StaffFace.staff_id == person_id, self._mine(m.StaffFace)))
            s.execute(update(m.Staff).where(m.Staff.id == person_id, self._mine(m.Staff)).values(active=False))

    # AI calls

    def record_ai_call(self, task: str = "ai") -> None:
        with self.tx() as s:
            s.add(m.AiCall(workspace_id=self.workspace_id, task=task))

    def count_ai_calls_today(self) -> int:
        """AI requests since midnight in the society's time zone."""
        with self.tx() as s:
            zone = s.scalar(select(m.Workspace.timezone).where(m.Workspace.id == self.workspace_id)) or "UTC"
            midnight = m.utcnow().astimezone(ZoneInfo(zone)).replace(hour=0, minute=0, second=0, microsecond=0)
            return s.scalar(select(func.count()).select_from(m.AiCall)
                            .where(self._mine(m.AiCall), m.AiCall.ts >= midnight)) or 0

    # names given to people on a camera

    def set_person_label(self, camera_id: str, group_id: str, label: str) -> None:
        with self.tx() as s:
            row = s.get(m.PersonLabel, (self.workspace_id, camera_id, group_id))
            if row is None:
                s.add(m.PersonLabel(workspace_id=self.workspace_id, camera_id=camera_id, group_id=group_id,
                                    label=label))
            else:
                row.label = label

    def person_labels(self, camera_id: str) -> dict[str, str]:
        with self.tx() as s:
            return dict(s.execute(select(m.PersonLabel.group_id, m.PersonLabel.label)
                                  .where(self._mine(m.PersonLabel), m.PersonLabel.camera_id == camera_id)).all())


def _as_stored(row: WatchRow) -> WatchRow:
    """The row as stored: columns that follow the watch spec take the spec's values."""
    cols = _watch_columns(row)
    row.scope, row.excluded_cameras = cols["scope"], cols["excluded_cameras_json"]
    row.output, row.severity = cols["output"], cols["severity"]
    return row


@register("repo", "sql", "sqlite", "postgresql")
def open_repo(engine: Engine, workspace_id: str, settings=None, name: str = "sql", **_) -> SqlRepository:
    secret = (settings.data_key or settings.session_secret) if settings is not None else None
    return SqlRepository(engine, workspace_id, secret=secret)
