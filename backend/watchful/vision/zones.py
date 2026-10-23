from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from watchful.vision.track import Track


@dataclass
class Zone:
    id: str
    name: str
    polygon: list[tuple[float, float]]  # normalized 0..1

    def contour(self, size: tuple[int, int]) -> np.ndarray:
        w, h = size
        return np.array([[x * w, y * h] for x, y in self.polygon], np.float32)

    def contains(self, point: tuple[float, float], size: tuple[int, int]) -> bool:
        return cv2.pointPolygonTest(self.contour(size), point, False) >= 0


@dataclass
class ZoneEvent:
    kind: Literal["enter", "exit"]
    zone: str
    track_id: int
    cls: str
    ts: float
    dwell_s: float | None = None


def foot_point(box) -> tuple[float, float]:
    x, y, w, h = box
    return (x + w / 2, y + h)


class ZoneTracker:
    def __init__(self, zones: list[Zone]):
        self.zones = zones
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
