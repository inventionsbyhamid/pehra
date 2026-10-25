"""What the engine stores and reads, as plain dataclasses, and the Repository it goes through. A repository is
bound to one workspace (a society) and never reads or changes another's rows (store/sql.py)."""
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone

DEFAULT_WORKSPACE = "default"  # the single society of the demo until sign in brings real ones


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
    workspace_id: str = DEFAULT_WORKSPACE
    purpose: str = "other"
    status: str = "offline"
    last_frame_at: float | None = None


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
    workspace_id: str = DEFAULT_WORKSPACE


@dataclass
class WatchRow:
    id: str | None
    camera_id: str
    text: str
    watch: dict
    enabled: bool = True
    workspace_id: str = DEFAULT_WORKSPACE
    # scope, excluded_cameras, output and severity follow the watch spec when it has them
    scope: Literal["camera", "site"] = "camera"
    excluded_cameras: list[str] = field(default_factory=list)
    output: Literal["alert", "log"] = "alert"
    severity: Literal["critical", "warning", "info"] = "info"
    coverage: list = field(default_factory=list)
    setup: list = field(default_factory=list)
    cannot: list = field(default_factory=list)


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
    workspace_id: str = DEFAULT_WORKSPACE


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
    def record_ai_call(self, task: str = "ai") -> None: ...
    def count_ai_calls_today(self) -> int: ...
    def set_person_label(self, camera_id: str, group_id: str, label: str) -> None: ...
    def person_labels(self, camera_id: str) -> dict[str, str]: ...


def SqliteRepository(path: str | Path, workspace_id: str = DEFAULT_WORKSPACE):
    """A repository on a SQLite file, its schema migrated and the default workspace made; for tests and tools."""
    from watchful.store.sql import open_sqlite
    return open_sqlite(path, workspace_id)
