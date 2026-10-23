from collections import deque
from dataclasses import dataclass
from typing import Literal

from watchful.tuning import FallTuning
from watchful.vision.pose import PoseResult

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

    def __init__(self, track_id: int = 0, cfg: FallTuning = FallTuning()):
        self.track_id = track_id
        self.cfg = cfg
        self.state: Literal["upright", "falling", "fallen"] = "upright"
        self.samples: deque[_Sample] = deque()
        self._lying_since: float | None = None
        self._up_since: float | None = None

    def update(self, ts: float, pose: PoseResult | None, box) -> PostureEvent | None:
        c = self.cfg
        x, y, w, h = box
        aspect = w / h if h else 0.0
        if pose is not None:
            s = _Sample(ts, pose.torso_angle_deg, pose.hip_y, pose.body_height, aspect, pose.confidence)
        else:
            s = _Sample(ts, None, y + h / 2, max(w, h), aspect, c.box_only_confidence)
        self.samples.append(s)
        while self.samples and self.samples[0].ts < ts - c.history_s:
            self.samples.popleft()

        lying = (s.angle is not None and s.angle > c.lying_angle) or aspect > c.lying_aspect
        up = (s.angle is None or s.angle < c.up_angle) and aspect < c.up_aspect
        self._lying_since = (self._lying_since if self._lying_since is not None else ts) if lying else None
        self._up_since = (self._up_since if self._up_since is not None else ts) if up else None
        conf = s.conf if s.angle is not None else c.box_only_confidence

        if self.state == "upright" and self._is_falling(s):
            self.state = "falling"
            return PostureEvent("falling", self.track_id, ts, conf)
        if self.state in ("upright", "falling") and self._lying_since is not None \
                and ts - self._lying_since >= c.hold_s:
            self.state = "fallen"
            return PostureEvent("fallen", self.track_id, ts, conf)
        if self.state == "falling" and self._up_since is not None and ts - self._up_since >= c.hold_s:
            self.state = "upright"
        if self.state == "fallen" and self._up_since is not None and ts - self._up_since >= c.hold_s:
            self.state = "upright"
            return PostureEvent("recovered", self.track_id, ts, conf)
        return None

    def _is_falling(self, s: _Sample) -> bool:
        c = self.cfg
        if s.angle is None:
            return False
        for p in self.samples:
            if not (s.ts - c.window_s <= p.ts < s.ts) or p.angle is None:
                continue
            drop_speed = (s.hip_y - p.hip_y) / max(p.height, 1.0) / max(s.ts - p.ts, 1e-3)
            if drop_speed > c.drop_speed and s.angle - p.angle > c.angle_rise:
                return True
        return False
