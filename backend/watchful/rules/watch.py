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
    op: Literal[">=", "<=", "==", "is"] = ">="
    value: float | bool | str = True
    weight: float = 1.0
    params: dict = {}


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
