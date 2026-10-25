from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from watchful.tuning import ZoneTuning
from watchful.vision.track import Track


@dataclass
class Zone:
    id: str
    name: str
    polygon: list[tuple[float, float]]  # normalized 0..1; a line has two points
    kind: str = "area"  # "area", "water" (only tunes defaults) or "line"

    @property
    def is_line(self) -> bool:
        return self.kind == "line"

    def contour(self, size: tuple[int, int]) -> np.ndarray:
        w, h = size
        return np.array([[x * w, y * h] for x, y in self.polygon], np.float32)

    def contains(self, point: tuple[float, float], size: tuple[int, int]) -> bool:
        if self.is_line:
            return False
        return cv2.pointPolygonTest(self.contour(size), point, False) >= 0


@dataclass
class ZoneEvent:
    kind: Literal["enter", "exit", "cross"]
    zone: str
    track_id: int
    cls: str
    ts: float
    dwell_s: float | None = None
    direction: str | None = None  # crossings: "in" or "out"


def foot_point(box) -> tuple[float, float]:
    x, y, w, h = box
    return (x + w / 2, y + h)


class ZoneTracker:
    def __init__(self, zones: list[Zone]):
        self.zones = [z for z in zones if not z.is_line]
        self._inside: dict[int, dict[str, float]] = {}

    def update(self, tracks: list[Track], ended: list[Track], ts: float,
               frame_size: tuple[int, int]) -> list[ZoneEvent]:
        events = []
        for t in tracks:
            now = self._inside.setdefault(t.id, {})
            foot = foot_point(t.box)
            for z in self.zones:
                inside = z.contains(foot, frame_size)
                if inside and z.name not in now:
                    now[z.name] = ts
                    events.append(ZoneEvent("enter", z.name, t.id, t.cls, ts))
                elif not inside and z.name in now:
                    entered = now.pop(z.name)
                    events.append(ZoneEvent("exit", z.name, t.id, t.cls, ts, ts - entered))
        for t in ended:
            for name, entered in self._inside.pop(t.id, {}).items():
                events.append(ZoneEvent("exit", name, t.id, t.cls, t.last_ts, t.last_ts - entered))
        return events

    def inside(self, track_id: int) -> dict[str, float]:
        return dict(self._inside.get(track_id, {}))


def line_position(line: Zone, point: tuple[float, float], size: tuple[int, int]) -> tuple[float, float]:
    """Signed distance in pixels from a line (positive on its inside: the right of its first point to its second,
    as seen on screen) and where the point falls along it (0 at the first point, 1 at the second)."""
    (ax, ay), (bx, by) = line.contour(size)[:2]
    dx, dy = float(bx - ax), float(by - ay)
    length = float(np.hypot(dx, dy)) or 1.0
    px, py = point[0] - ax, point[1] - ay
    return (dx * py - dy * px) / length, (dx * px + dy * py) / length ** 2


class LineTracker:
    """Directed crossings of drawn lines by a track's foot point. A side only changes once the point is clearly
    past the line, so feet jittering on it never count twice."""

    def __init__(self, zones: list[Zone], cfg: ZoneTuning = ZoneTuning()):
        self.lines = [z for z in zones if z.is_line and len(z.polygon) >= 2]
        self.cfg = cfg
        self._side: dict[tuple[int, str], int] = {}

    def update(self, tracks: list[Track], ended: list[Track], ts: float,
               frame_size: tuple[int, int]) -> list[ZoneEvent]:
        events = []
        for t in tracks:
            foot = foot_point(t.box)
            margin = self.cfg.line_hysteresis * max(t.box[3], 1.0)
            for line in self.lines:
                dist, along = line_position(line, foot, frame_size)
                if abs(dist) < margin:
                    continue
                side, key = (1 if dist > 0 else -1), (t.id, line.name)
                before = self._side.get(key)
                self._side[key] = side
                within = -self.cfg.line_extend <= along <= 1 + self.cfg.line_extend
                if before is not None and before != side and within:
                    events.append(ZoneEvent("cross", line.name, t.id, t.cls, ts,
                                            direction="in" if side > 0 else "out"))
        for t in ended:
            for line in self.lines:
                self._side.pop((t.id, line.name), None)
        return events
