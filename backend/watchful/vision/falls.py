from collections import deque
from dataclasses import dataclass
from typing import Literal

from watchful.vision.pose import PoseResult

DROP_SPEED = 0.6  # body heights per second
ANGLE_RISE = 30.0  # degrees within WINDOW_S
WINDOW_S = 1.0
LYING_ANGLE = 60.0
LYING_ASPECT = 1.3
HOLD_S = 2.0
UP_ANGLE = 35.0
UP_ASPECT = 1.0
BOX_ONLY_CONFIDENCE = 0.5


@dataclass
class PostureEvent:
    kind: Literal["falling", "fallen", "recovered"]
    track_id: int
    ts: float
    confidence: float


@dataclass
class _Sample:
    ts: float
    angle: float | None
    hip_y: float
    height: float
    aspect: float
    conf: float


class FallDetector:
    """Posture state per person track: upright, falling, fallen."""

    def __init__(self, track_id: int = 0, rate_hz: float = 2.0):
        self.track_id = track_id
        self.rate_hz = rate_hz
        self.state: Literal["upright", "falling", "fallen"] = "upright"
        self.samples: deque[_Sample] = deque()
        self._lying_since: float | None = None
        self._up_since: float | None = None

    def update(self, ts: float, pose: PoseResult | None, box) -> PostureEvent | None:
        x, y, w, h = box
        aspect = w / h if h else 0.0
        if pose is not None:
            s = _Sample(ts, pose.torso_angle_deg, pose.hip_y, pose.body_height, aspect, pose.confidence)
        else:
            s = _Sample(ts, None, y + h / 2, max(w, h), aspect, BOX_ONLY_CONFIDENCE)
        self.samples.append(s)
        while self.samples and self.samples[0].ts < ts - 3.0:
            self.samples.popleft()

        lying = (s.angle is not None and s.angle > LYING_ANGLE) or aspect > LYING_ASPECT
        up = (s.angle is None or s.angle < UP_ANGLE) and aspect < UP_ASPECT
        self._lying_since = (self._lying_since if self._lying_since is not None else ts) if lying else None
        self._up_since = (self._up_since if self._up_since is not None else ts) if up else None
        conf = s.conf if s.angle is not None else BOX_ONLY_CONFIDENCE

        if self.state == "upright" and self._is_falling(s):
            self.state = "falling"
            return PostureEvent("falling", self.track_id, ts, conf)
        if self.state in ("upright", "falling") and self._lying_since is not None \
                and ts - self._lying_since >= HOLD_S:
            self.state = "fallen"
            return PostureEvent("fallen", self.track_id, ts, conf)
        if self.state == "falling" and self._up_since is not None and ts - self._up_since >= HOLD_S:
            self.state = "upright"
        if self.state == "fallen" and self._up_since is not None and ts - self._up_since >= HOLD_S:
            self.state = "upright"
            return PostureEvent("recovered", self.track_id, ts, conf)
        return None

    def _is_falling(self, s: _Sample) -> bool:
        if s.angle is None:
            return False
        for p in self.samples:
            if not (s.ts - WINDOW_S <= p.ts < s.ts) or p.angle is None:
                continue
            drop_speed = (s.hip_y - p.hip_y) / max(p.height, 1.0) / max(s.ts - p.ts, 1e-3)
            if drop_speed > DROP_SPEED and s.angle - p.angle > ANGLE_RISE:
                return True
        return False
