import json
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from watchful.registry import register
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass
class Camera:
    id: str | None
    name: str
    kind: Literal["stream", "webcam", "file", "demo"]
    url: str | None
    zones: list[Zone] = field(default_factory=list)
    enabled: bool = True
    tuning: dict = field(default_factory=dict)
    last_scan: dict | None = None


@dataclass
class EventRow:
    id: str | None
    camera_id: str
    ts: float
    kind: str
    track_id: int | None
    cls: str | None
    zone: str | None
    data: dict
    keyframe_key: str | None
    signature: list[float] | None
    embedding: list[float] | None
    person_label: str | None


@dataclass
class WatchRow:
    id: str | None
    camera_id: str
    text: str
    watch: dict
    enabled: bool = True


@dataclass
class AlertRow:
    id: str | None
    camera_id: str
    watch_id: str | None
    ts: float
    severity: str
    status: Literal["alert", "unverified", "dismissed", "awaiting_human", "approved", "declined"]
    summary: str
    clip_key: str | None
    keyframe_key: str | None
    trace: list[dict]
    feedback: Literal["up", "down"] | None
    fingerprint: str
    track_id: int | None = None


class Repository(Protocol):
    def add_camera(self, cam: Camera) -> Camera: ...
    def get_camera(self, camera_id: str) -> Camera | None: ...
    def list_cameras(self) -> list[Camera]: ...
    def update_camera(self, cam: Camera) -> None: ...
    def delete_camera(self, camera_id: str) -> None: ...
    def delete_camera_events(self, camera_id: str) -> int: ...
    def add_events(self, rows: list[EventRow]) -> None: ...
    def query_events(self, camera_id: str | None = None, kinds: list[str] | None = None, zone: str | None = None,
                     cls: str | None = None, since: float | None = None, until: float | None = None,
                     limit: int = 500) -> list[EventRow]: ...
    def get_event(self, event_id: str) -> EventRow | None: ...
    def events_missing_embedding(self, kinds: list[str], limit: int = 64) -> list[EventRow]: ...
    def clear_embeddings(self) -> int: ...
    def get_meta(self, key: str) -> str | None: ...
    def set_meta(self, key: str, value: str) -> None: ...
    def set_event_embedding(self, event_id: str, embedding: list[float]) -> None: ...
    def delete_events_before(self, ts: float, keep_cameras: list[str] = ()) -> int: ...
    def delete_alerts_before(self, ts: float, keep_cameras: list[str] = ()) -> int: ...
    def add_watch(self, row: WatchRow) -> WatchRow: ...
    def list_watches(self, camera_id: str | None = None) -> list[WatchRow]: ...
    def update_watch(self, row: WatchRow) -> None: ...
    def add_alert(self, row: AlertRow) -> AlertRow: ...
    def get_alert(self, alert_id: str) -> AlertRow | None: ...
    def update_alert(self, row: AlertRow) -> None: ...
    def list_alerts(self, camera_id: str | None = None, limit: int = 100) -> list[AlertRow]: ...
    def alert_fingerprint_exists(self, fp: str, within_s: float) -> bool: ...
    def add_enrollment(self, e: Enrollment) -> None: ...
    def list_enrollments(self) -> list[Enrollment]: ...
    def delete_enrollment(self, person_id: str) -> None: ...
    def record_ai_call(self) -> None: ...
    def count_ai_calls_today(self) -> int: ...
    def set_person_label(self, camera_id: str, group_id: str, label: str) -> None: ...
    def person_labels(self, camera_id: str) -> dict[str, str]: ...


def _camera_from(d: dict) -> Camera:
    d = dict(d)
    d["zones"] = [Zone(z["id"], z["name"], [tuple(p) for p in z["polygon"]]) for z in d.get("zones", [])]
    return Camera(**d)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


SCHEMA = """
create table if not exists cameras (id text primary key, data text not null);
create table if not exists events (id text primary key, camera_id text, ts real, kind text, zone text, cls text,
                                   data text not null);
create index if not exists events_cam_ts on events (camera_id, ts);
create table if not exists watches (id text primary key, camera_id text, data text not null);
create table if not exists alerts (id text primary key, camera_id text, ts real, fingerprint text, data text not null);
create index if not exists alerts_fp on alerts (fingerprint, ts);
create table if not exists enrollments (person_id text primary key, data text not null);
create table if not exists counters (day text, name text, n integer, primary key (day, name));
create table if not exists meta (key text primary key, value text);
create table if not exists person_labels (camera_id text, group_id text, label text, primary key (camera_id, group_id));
"""


@register("repo", "sqlite")
class SqliteRepository:
    def __init__(self, path: str | Path, name: str = "sqlite", **_):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        columns = {r[1] for r in self.db.execute("pragma table_info(events)")}
        if "embedded" not in columns:
            self.db.execute("alter table events add column embedded integer default 0")
        self.lock = threading.Lock()

    def _write(self, sql: str, args=()) -> None:
        with self.lock, self.db:
            self.db.execute(sql, args)

    def _rows(self, sql: str, args=()) -> list[tuple]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def add_camera(self, cam: Camera) -> Camera:
        cam.id = cam.id or new_id("cam")
        self._write("insert into cameras values (?, ?)", (cam.id, json.dumps(asdict(cam))))
        return cam

    def get_camera(self, camera_id: str) -> Camera | None:
        rows = self._rows("select data from cameras where id = ?", (camera_id,))
        return _camera_from(json.loads(rows[0][0])) if rows else None

    def list_cameras(self) -> list[Camera]:
        return [_camera_from(json.loads(r[0])) for r in self._rows("select data from cameras order by rowid")]

    def update_camera(self, cam: Camera) -> None:
        self._write("update cameras set data = ? where id = ?", (json.dumps(asdict(cam)), cam.id))

    def delete_camera(self, camera_id: str) -> None:
        with self.lock, self.db:
            for table in ("events", "watches", "alerts"):
                self.db.execute(f"delete from {table} where camera_id = ?", (camera_id,))
            self.db.execute("delete from person_labels where camera_id = ?", (camera_id,))
            self.db.execute("delete from cameras where id = ?", (camera_id,))

    def delete_camera_events(self, camera_id: str) -> int:
        with self.lock, self.db:
            return self.db.execute("delete from events where camera_id = ?", (camera_id,)).rowcount

    def add_events(self, rows: list[EventRow]) -> None:
        with self.lock, self.db:
            for r in rows:
                r.id = r.id or new_id("evt")
                self.db.execute("insert into events (id, camera_id, ts, kind, zone, cls, data, embedded) "
                                "values (?, ?, ?, ?, ?, ?, ?, ?)",
                                (r.id, r.camera_id, r.ts, r.kind, r.zone, r.cls, json.dumps(asdict(r)),
                                 int(r.embedding is not None)))

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None,
                     limit=500) -> list[EventRow]:
        sql, args = "select data from events where 1=1", []
        for column, value in (("camera_id", camera_id), ("zone", zone), ("cls", cls)):
            if value is not None:
                sql += f" and {column} = ?"
                args.append(value)
        if kinds:
            sql += f" and kind in ({','.join('?' * len(kinds))})"
            args += kinds
        if since is not None:
            sql += " and ts >= ?"
            args.append(since)
        if until is not None:
            sql += " and ts <= ?"
            args.append(until)
        sql += " order by ts desc limit ?"  # the newest rows, returned oldest first
        args.append(limit)
        return [EventRow(**json.loads(r[0])) for r in reversed(self._rows(sql, args))]

    def get_event(self, event_id: str) -> EventRow | None:
        rows = self._rows("select data from events where id = ?", (event_id,))
        return EventRow(**json.loads(rows[0][0])) if rows else None

    def events_missing_embedding(self, kinds: list[str], limit: int = 64) -> list[EventRow]:
        rows = self._rows(f"select data from events where embedded = 0 and kind in ({','.join('?' * len(kinds))}) "
                          "order by ts desc limit ?", [*kinds, limit])
        return [EventRow(**json.loads(r[0])) for r in rows]

    def clear_embeddings(self) -> int:
        with self.lock, self.db:
            rows = self.db.execute("select id, data from events where embedded = 1").fetchall()
            for event_id, data in rows:
                d = json.loads(data)
                d["embedding"] = None
                self.db.execute("update events set data = ?, embedded = 0 where id = ?", (json.dumps(d), event_id))
            return len(rows)

    def get_meta(self, key: str) -> str | None:
        rows = self._rows("select value from meta where key = ?", (key,))
        return rows[0][0] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._write("insert or replace into meta values (?, ?)", (key, value))

    def set_event_embedding(self, event_id: str, embedding: list[float]) -> None:
        with self.lock, self.db:
            row = self.db.execute("select data from events where id = ?", (event_id,)).fetchone()
            if row:
                data = json.loads(row[0])
                data["embedding"] = [round(float(x), 5) for x in embedding]
                self.db.execute("update events set data = ?, embedded = 1 where id = ?", (json.dumps(data), event_id))

    def delete_events_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        keep = list(keep_cameras)
        with self.lock, self.db:
            return self.db.execute(f"delete from events where ts < ? and camera_id not in ({','.join('?' * len(keep))})",
                                   (ts, *keep)).rowcount

    def delete_alerts_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        keep = list(keep_cameras)
        with self.lock, self.db:
            return self.db.execute(f"delete from alerts where ts < ? and camera_id not in ({','.join('?' * len(keep))})",
                                   (ts, *keep)).rowcount

    def add_watch(self, row: WatchRow) -> WatchRow:
        row.id = row.id or new_id("wat")
        self._write("insert into watches values (?, ?, ?)", (row.id, row.camera_id, json.dumps(asdict(row))))
        return row

    def list_watches(self, camera_id=None) -> list[WatchRow]:
        if camera_id is None:
            rows = self._rows("select data from watches order by rowid")
        else:
            rows = self._rows("select data from watches where camera_id = ? order by rowid", (camera_id,))
        return [WatchRow(**json.loads(r[0])) for r in rows]

    def update_watch(self, row: WatchRow) -> None:
        self._write("update watches set data = ? where id = ?", (json.dumps(asdict(row)), row.id))

    def add_alert(self, row: AlertRow) -> AlertRow:
        row.id = row.id or new_id("alt")
        self._write("insert into alerts values (?, ?, ?, ?, ?)",
                    (row.id, row.camera_id, row.ts, row.fingerprint, json.dumps(asdict(row))))
        return row

    def get_alert(self, alert_id: str) -> AlertRow | None:
        rows = self._rows("select data from alerts where id = ?", (alert_id,))
        return AlertRow(**json.loads(rows[0][0])) if rows else None

    def update_alert(self, row: AlertRow) -> None:
        self._write("update alerts set data = ? where id = ?", (json.dumps(asdict(row)), row.id))

    def list_alerts(self, camera_id=None, limit=100) -> list[AlertRow]:
        if camera_id is None:
            rows = self._rows("select data from alerts order by ts desc limit ?", (limit,))
        else:
            rows = self._rows("select data from alerts where camera_id = ? order by ts desc limit ?",
                              (camera_id, limit))
        return [AlertRow(**json.loads(r[0])) for r in rows]

    def alert_fingerprint_exists(self, fp: str, within_s: float) -> bool:
        rows = self._rows("select 1 from alerts where fingerprint = ? and ts >= ? limit 1",
                          (fp, time.time() - within_s))
        return bool(rows)

    def add_enrollment(self, e: Enrollment) -> None:
        self._write("insert or replace into enrollments values (?, ?)", (e.person_id, json.dumps(asdict(e))))

    def list_enrollments(self) -> list[Enrollment]:
        return [Enrollment(**json.loads(r[0])) for r in self._rows("select data from enrollments order by rowid")]

    def delete_enrollment(self, person_id: str) -> None:
        self._write("delete from enrollments where person_id = ?", (person_id,))

    def record_ai_call(self) -> None:
        self._write("insert into counters values (?, 'ai_calls', 1) "
                    "on conflict (day, name) do update set n = n + 1", (_today(),))

    def count_ai_calls_today(self) -> int:
        rows = self._rows("select n from counters where day = ? and name = 'ai_calls'", (_today(),))
        return rows[0][0] if rows else 0

    def set_person_label(self, camera_id: str, group_id: str, label: str) -> None:
        self._write("insert or replace into person_labels values (?, ?, ?)", (camera_id, group_id, label))

    def person_labels(self, camera_id: str) -> dict[str, str]:
        return dict(self._rows("select group_id, label from person_labels where camera_id = ?", (camera_id,)))

