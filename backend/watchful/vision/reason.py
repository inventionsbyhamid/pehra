from dataclasses import dataclass, field

import numpy as np

from watchful.store.db import Camera, EventRow
from watchful.tuning import Tuning
from watchful.vision.falls import FallDetector, PostureEvent
from watchful.vision.perceive import Observation, Perception
from watchful.vision.pose import PoseResult
from watchful.vision.zones import ZoneEvent, ZoneTracker


@dataclass
class TrackState:
    track_id: int
    cls: str
    box: tuple[float, float, float, float]
    first_ts: float
    last_ts: float
    zones_inside: dict[str, float] = field(default_factory=dict)
    pose: PoseResult | None = None  # latest estimate
    fresh_pose: bool = False
    posture: str = "upright"
    known_person_id: str | None = None
    known_name: str | None = None
    signature: np.ndarray | None = None
    embedding: np.ndarray | None = None
    keyframe_key: str | None = None
    best_key: str | None = None


@dataclass
class Tick:
    ts: float
    size: tuple[int, int]
    moving: bool
    tracks: list[TrackState]
    events: list[EventRow]
    ended: list[TrackState] = field(default_factory=list)


@dataclass
class _Box:
    """What ZoneTracker reads from a track."""
    id: int
    cls: str
    box: tuple
    last_ts: float


def _list(v) -> list[float] | None:
    return None if v is None else np.asarray(v, np.float32).reshape(-1).tolist()


class Reasoner:
    """Cheap and deterministic: zones, posture and events from observations, so scans can replay it."""

    def __init__(self, camera: Camera, tuning: Tuning, postures: bool = True):
        self.camera, self.t, self.postures = camera, tuning, postures
        self.zones = ZoneTracker(camera.zones)
        self.states: dict[int, TrackState] = {}
        self.falls: dict[int, FallDetector] = {}

    def step(self, p: Perception) -> Tick:
        events: list[EventRow] = []
        active, ended = [], []
        for o in p.observations:
            s = self.states.get(o.track_id)
            if s is None:
                s = self.states[o.track_id] = TrackState(o.track_id, o.cls, o.box, o.ts, o.ts)
            self._apply(s, o)
            (ended if o.ended else active).append(s)
            if o.started:
                events.append(self._event(s, "track_start", o.ts))

        zone_events = self.zones.update([_Box(s.track_id, s.cls, s.box, s.last_ts) for s in active],
                                        [_Box(s.track_id, s.cls, s.box, s.last_ts) for s in ended], p.ts, p.size)
        for s in active:
            s.zones_inside = self.zones.inside(s.track_id)
        for z in zone_events:
            s = self.states[z.track_id]
            events.append(self._event(s, z.kind, z.ts, z.zone, {"dwell_s": z.dwell_s} if z.dwell_s is not None else {}))

        if self.postures:
            for s in active:
                if s.cls != "person":
                    continue
                fd = self.falls.setdefault(s.track_id, FallDetector(s.track_id, self.t.falls))
                e = fd.update(p.ts, s.pose if s.fresh_pose else None, s.box)
                s.posture = fd.state
                if e:
                    events.append(self._posture_event(s, e))

        for s in ended:
            events.append(self._event(s, "track_end", s.last_ts, data={"duration_s": s.last_ts - s.first_ts}))
            self.states.pop(s.track_id, None)
            self.falls.pop(s.track_id, None)
        if p.tamper:
            events.append(EventRow(None, self.camera.id, p.ts, f"tamper_{p.tamper.kind}", None, None, None,
                                   {"detail": p.tamper.detail}, None, None, None, None))
        return Tick(p.ts, p.size, p.moving, active, events, ended)

    @staticmethod
    def _apply(s: TrackState, o: Observation) -> None:
        s.box, s.cls = o.box, o.cls
        if not o.ended:
            s.last_ts = o.ts
        s.fresh_pose = o.pose is not None
        if o.pose is not None:
            s.pose = o.pose
        if o.face is not None:
            s.known_person_id, s.known_name = o.face.person_id, o.face.name
        for name in ("signature", "embedding", "keyframe_key", "best_key"):
            value = getattr(o, name)
            if value is not None:
                setattr(s, name, value)

    def _event(self, s: TrackState, kind: str, ts: float, zone: str | None = None, data: dict | None = None) -> EventRow:
        person = s.cls == "person"
        return EventRow(None, self.camera.id, ts, kind, s.track_id, s.cls, zone, data or {},
                        s.best_key or s.keyframe_key, _list(s.signature) if person else None,
                        _list(s.embedding) if person else None, s.known_name)

    def _posture_event(self, s: TrackState, e: PostureEvent) -> EventRow:
        return self._event(s, e.kind, e.ts, next(iter(s.zones_inside), None), {"confidence": e.confidence})


__all__ = ["Reasoner", "Tick", "TrackState", "ZoneEvent"]
