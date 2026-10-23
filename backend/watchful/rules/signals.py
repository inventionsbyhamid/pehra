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
    same_threshold: float = 0.35  # clothing distance under which two sightings look alike

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


@signal("posture", "track", "text", "A person's posture: upright, falling or fallen.", needs=("posture",))
def posture(ctx, cond, track):
    return SignalValue(track.posture, None if track.posture == "upright" else f"posture {track.posture}")


@signal("count", "scene", "number", "How many subjects are inside the zones (or in view when no zones).")
def count(ctx, cond, track):
    n = sum(1 for t in ctx.subject_tracks if not cond.zones or _in(t, cond.zones))
    where = f" in {cond.zones[0]}" if cond.zones else ""
    return SignalValue(float(n), f"{n} seen{where}")


L_WRIST, R_WRIST, NOSE = 15, 16, 0
VISIBLE = 0.5


@signal("zones_visited", "track", "number",
        "How many different zones the subject has been in within params.window_s seconds (default 300).")
def zones_visited(ctx, cond, track):
    h = ctx.history.get(track.track_id)
    window = float(cond.params.get("window_s", 300))
    names = set(cond.zones) if cond.zones else None
    seen: set[str] = set()
    for ts, zones in (h.zones if h else []):
        if ts >= ctx.tick.ts - window:
            seen |= {z for z in zones if names is None or z in names}
    return SignalValue(float(len(seen)), f"went to {' and '.join(sorted(seen))}" if len(seen) > 1 else None)


def _wrists_in(ctx, pose, zones: list[str]) -> str | None:
    lm = pose.landmarks
    if lm is None:
        return None
    for i in (L_WRIST, R_WRIST):
        if lm[i, 3] < VISIBLE:
            continue
        point = (float(lm[i, 0]), float(lm[i, 1]))
        for name in zones or list(ctx.zones):
            if ctx.zones[name].contains(point, ctx.tick.size):
                return name
    return None


@signal("hands_in_zone_s", "track", "number", "Seconds a person's hands were inside the zones in the last minute.",
        needs=("pose",))
def hands_in_zone_s(ctx, cond, track):
    h = ctx.history.get(track.track_id)
    poses = [(ts, p) for ts, p in (h.poses if h else []) if ts >= ctx.tick.ts - 60]
    total, where = 0.0, None
    for (t0, p0), (t1, p1) in zip(poses, poses[1:]):
        z = _wrists_in(ctx, p1, cond.zones)
        if z and _wrists_in(ctx, p0, cond.zones):
            total += t1 - t0
            where = z
    return SignalValue(total, f"hands at {where} for {total:.0f} s" if where else None)


@signal("climbing", "track", "bool",
        "A person climbs inside the zones: hips rise quickly, or both hands are above the head while rising.",
        needs=("pose",))
def climbing(ctx, cond, track):
    if cond.zones and not _in(track, cond.zones):
        return SignalValue(False)
    h = ctx.history.get(track.track_id)
    window, rise_needed = ctx.tuning.climb_window_s, ctx.tuning.climb_rise
    poses = [(ts, p) for ts, p in (h.poses if h else []) if ts >= ctx.tick.ts - window]
    if len(poses) < 2:
        return SignalValue(False)
    (_, first), (_, last) = poses[0], poses[-1]
    rise = (first.hip_y - last.hip_y) / max(first.body_height, 1.0)
    lm = last.landmarks
    hands_up = lm is not None and all(lm[i, 1] < lm[NOSE, 1] and lm[i, 3] >= VISIBLE for i in (L_WRIST, R_WRIST))
    ok = rise >= rise_needed or (hands_up and rise >= rise_needed / 2)
    where = _in(track, cond.zones)
    return SignalValue(ok, f"climbing at {where[0] if where else 'the edge'}" if ok else None)


@signal("returns", "track", "number",
        "How many times a similar looking person not enrolled has come within params.window_s (default 1800), "
        "counting this visit.")
def returns(ctx, cond, track):
    from watchful.vision.appearance import same_person
    if track.signature is None or track.known_person_id:
        return SignalValue(1.0)
    window = float(cond.params.get("window_s", 1800))
    same = ctx.same_threshold
    n = 1 + sum(1 for s in ctx.sightings if not s.known and ctx.tick.ts - s.last_ts <= window
                and s.track_id != track.track_id and same_person(s.signature, track.signature) < same)
    return SignalValue(float(n), f"visit number {n} in {window / 60:.0f} minutes" if n > 1 else None)


@signal("zone_looks_like", "scene", "bool",
        "The view inside a zone matches params.text better than params.not_text for params.hold_s seconds "
        "(open vocabulary, for example 'an open gate' against 'a closed gate').", needs=("embed",))
def zone_looks_like(ctx, cond, track):
    import numpy as np
    text, not_text = cond.params.get("text"), cond.params.get("not_text")
    if ctx.embedder is None or ctx.image is None or not text or not cond.zones:
        return SignalValue(False)
    st = ctx.state.setdefault(f"zone_looks_like:{cond.zones[0]}:{text}", {})
    now = ctx.tick.ts
    if "text" not in st:
        st["text"], st["not"] = ctx.embedder.text(text), ctx.embedder.text(not_text) if not_text else None
    if now - st.get("checked", float("-inf")) >= ctx.tuning.zone_embed_every_s:
        st["checked"] = now
        zone = ctx.zones[cond.zones[0]]
        pts = zone.contour(ctx.tick.size).astype(np.int32)
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        crop = ctx.image[max(0, y0):y1, max(0, x0):x1]
        if crop.size:
            v = ctx.embedder.image(crop)
            score = float(np.dot(v, st["text"])) - (float(np.dot(v, st["not"])) if st["not"] is not None else 0.0)
            matched = score >= ctx.tuning.zone_margin
            st["since"] = (st.get("since") or now) if matched else None
    since = st.get("since")
    hold = float(cond.params.get("hold_s", 0))
    ok = since is not None and now - since >= hold
    return SignalValue(ok, f"{cond.zones[0]} looks like {text} for {now - since:.0f} s" if ok else None)
