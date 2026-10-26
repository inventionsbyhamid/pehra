from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeWindow(_Model):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    tz: str | None = None

    def contains(self, ts: float, default_tz: str) -> bool:
        local = datetime.fromtimestamp(ts, ZoneInfo(self.tz or default_tz)).strftime("%H:%M")
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


class SameAs(_Model):
    event_id: str | None = None
    signature: list[float] | None = None
    embedding: list[float] | None = None
    crop_key: str | None = None
    expires_at: float | None = None


class Subject(_Model):
    classes: list[str] = ["person"]
    who: str = "anyone"  # anyone, unknown, known, or an enrolled person id
    looks_like: str | None = None
    looks_like_embedding: list[float] | None = None
    same_as: SameAs | None = None


class Condition(_Model):
    signal: str
    zones: list[str] = []
    op: Literal[">=", "<=", "==", "is", "!=", "contains", "in"] = ">="
    value: float | bool | str | list[str] = True
    weight: float = 1.0
    params: dict = {}  # signal settings; params.hold_s holds any condition true for that many seconds


class Watch(_Model):
    name: str
    subject: Subject = Subject()
    zones: list[str] = []
    conditions: list[Condition] = Field(min_length=1)
    combine: Literal["all", "any", "score"] = "all"
    threshold: float = 1.0
    time_window: TimeWindow | None = None
    severity: Literal["info", "warning", "critical"] = "info"
    verify: str = ""
    cooldown_s: float = 120
    repeat: bool = False  # keep firing every cooldown while true; by default once each time it becomes true
    expires_at: float | None = None
    scope: Literal["camera", "site"] = "camera"  # a site watch runs on every camera, also ones added later
    excluded_cameras: list[str] = []
    output: Literal["alert", "log"] = "alert"  # a log writes a row (time, camera, reasons) and never alerts

    def applies_to(self, camera_id: str, home_camera_id: str | None) -> bool:
        if self.scope == "site":
            return camera_id not in self.excluded_cameras
        return camera_id == home_camera_id


def select_watches(rows, camera_id: str, zones, entry: bool = True) -> tuple[list[tuple[str, Watch]], list[str]]:
    """The enabled watches that can run on a camera (its own and every site watch that does not exclude it), and
    a plain note for each that applies but can't run there (a zone or line this camera doesn't have, or who someone
    is on a camera that is not an entry: `entry` is False there, logs/readers.py entry_camera)."""
    from watchful.rules.engine import validate
    kinds = {z.name: z.kind for z in zones}
    ok, notes = [], []
    for row in rows:
        if not row.enabled:
            continue
        try:
            w = Watch.model_validate(row.watch)
        except ValueError as e:
            notes.append(f"{row.text}: {str(e).splitlines()[0]}")
            continue
        if not w.applies_to(camera_id, row.camera_id):
            continue
        if w.subject.who != "anyone" and not entry:
            notes.append(f"{w.name}: who someone is is only told on entry cameras, so it does not run here")
            continue
        issues = validate(w, kinds)
        if issues:
            notes.append(f"{w.name}: {'; '.join(issues)}")
        else:
            ok.append((row.id, w))
    return ok, notes
