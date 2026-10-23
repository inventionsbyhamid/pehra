"""Signals a watch can use. Adding a signal is one decorated function; the compiler reads the catalog."""
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Literal

from watchful.rules.watch import Condition
from watchful.store.db import EventRow
from watchful.tuning import SignalTuning
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone


@dataclass
class SignalValue:
    value: float | bool | str
    reason: str | None = None


@dataclass
class TrackHistory:
    boxes: deque = field(default_factory=lambda: deque(maxlen=600))  # (ts, box)
    zones: deque = field(default_factory=lambda: deque(maxlen=3000))  # (ts, frozenset of zones)
    poses: deque = field(default_factory=lambda: deque(maxlen=600))  # (ts, PoseResult)
    hands: dict = field(default_factory=dict)  # zone to seconds with a wrist inside
    last_ts: float | None = None


@dataclass
class Sighting:
    track_id: int
    first_ts: float
    last_ts: float
    signature: object
    known: bool


@dataclass
class SignalContext:
    camera_id: str
    zones: dict[str, Zone]
    tick: Tick
    tuning: SignalTuning
    history: dict[int, TrackHistory]
    sightings: list[Sighting]
    subject_tracks: list[TrackState] = field(default_factory=list)
    embedder: object | None = None
    image: object | None = None
    state: dict = field(default_factory=dict)  # per watch scratch space for scene signals

    def events(self, kind: str, track_id: int) -> list[EventRow]:
        return [e for e in self.tick.events if e.kind == kind and e.track_id == track_id]


Fn = Callable[[SignalContext, Condition, TrackState | None], SignalValue]


@dataclass
class SignalSpec:
    name: str
    level: Literal["track", "scene"]
    value: Literal["bool", "number", "text"]
    description: str
    needs: frozenset[str]
    fn: Fn


REGISTRY: dict[str, SignalSpec] = {}


def signal(name: str, level: Literal["track", "scene"], value: Literal["bool", "number", "text"],
           description: str, needs: tuple[str, ...] = ()):
    def register(fn: Fn) -> Fn:
        REGISTRY[name] = SignalSpec(name, level, value, description, frozenset(needs), fn)
        return fn
    return register


def catalog() -> list[dict]:
    return [{"signal": s.name, "level": s.level, "value": s.value, "description": s.description}
            for s in REGISTRY.values()]


def _in(track: TrackState, zones: list[str]) -> list[str]:
    return [z for z in (zones or list(track.zones_inside)) if z in track.zones_inside]


@signal("present", "track", "bool", "The subject is in view (inside the watch zones when the watch has zones).")
def present(ctx, cond, track):
    return SignalValue(True, "seen")


@signal("entered", "track", "bool", "The subject came into one of the zones on this moment.")
def entered(ctx, cond, track):
    hits = [e.zone for e in ctx.events("enter", track.track_id) if not cond.zones or e.zone in cond.zones]
    return SignalValue(bool(hits), f"came into {hits[0]}" if hits else None)


@signal("exited", "track", "bool", "The subject left one of the zones on this moment.")
def exited(ctx, cond, track):
    hits = [e.zone for e in ctx.events("exit", track.track_id) if not cond.zones or e.zone in cond.zones]
    return SignalValue(bool(hits), f"left {hits[0]}" if hits else None)


@signal("in_zone", "track", "bool", "The subject is inside one of the zones.")
def in_zone(ctx, cond, track):
    inside = _in(track, cond.zones)
    return SignalValue(bool(inside), f"in {inside[0]}" if inside else None)


@signal("dwell_s", "track", "number", "Seconds the subject has stayed inside one of the zones without leaving.")
def dwell_s(ctx, cond, track):
    inside = _in(track, cond.zones)
    if not inside:
        return SignalValue(0.0)
    zone = max(inside, key=lambda z: ctx.tick.ts - track.zones_inside[z])
    seconds = ctx.tick.ts - track.zones_inside[zone]
    return SignalValue(seconds, f"stayed in {zone} for {seconds:.0f} s")


@signal("speed", "track", "number", "How fast the subject moves, in its own body heights per second.")
def speed(ctx, cond, track):
    h = ctx.history.get(track.track_id)
    window = float(cond.params.get("window_s", 1.0))
    if not h or len(h.boxes) < 2:
        return SignalValue(0.0)
    ts, box = h.boxes[-1]
    old = next(((t, b) for t, b in h.boxes if t >= ts - window), h.boxes[0])
    dt = ts - old[0]
    if dt <= 0:
        return SignalValue(0.0)
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    ox, oy = old[1][0] + old[1][2] / 2, old[1][1] + old[1][3] / 2
    v = ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5 / max(box[3], 1.0) / dt
    return SignalValue(v, f"moving at {v:.1f} body heights per second")


@signal("posture", "track", "text", "A person's posture: upright, falling or fallen.", needs=("pose",))
def posture(ctx, cond, track):
    return SignalValue(track.posture, None if track.posture == "upright" else f"posture {track.posture}")


@signal("count", "scene", "number", "How many subjects are inside the zones (or in view when no zones).")
def count(ctx, cond, track):
    n = sum(1 for t in ctx.subject_tracks if not cond.zones or _in(t, cond.zones))
    where = f" in {cond.zones[0]}" if cond.zones else ""
    return SignalValue(float(n), f"{n} seen{where}")
