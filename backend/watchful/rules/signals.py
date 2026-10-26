"""Signals a watch can use: generic building blocks that any watch combines. Adding a signal is one decorated
function; the compiler reads the catalog, with each signal's reliability tier and caveat."""
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Literal

import cv2
import numpy as np

from watchful.rules.watch import Condition
from watchful.store.db import EventRow
from watchful.tuning import SignalTuning
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone, foot_point


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
    anchor: tuple | None = None  # (since, cx, cy): where the box settled, for stationary_s
    template: np.ndarray | None = None  # edge picture of a settled box, refreshed on the slow tier
    still: dict = field(default_factory=dict)  # optical flow state for still_s


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
    slow: bool = False  # this tick is on the slow zone tier
    health: str = "live"
    states: object | None = None  # saved zone state examples (rules.zonestate.ZoneStates)
    classes: list[str] = field(default_factory=list)  # the watch's subject classes, for scene signals
    shared: dict = field(default_factory=dict)  # per camera, shared by every watch: zone states, the flow tool

    def events(self, kind: str, track_id: int) -> list[EventRow]:
        return [e for e in self.tick.events if e.kind == kind and e.track_id == track_id]


Fn = Callable[[SignalContext, Condition, TrackState | None], SignalValue]
Tier = Literal["A", "B", "C", "X"]  # reliable, approximate, the AI checks every time, not watched
TIERS = ("A", "B", "C", "X")


@dataclass
class SignalSpec:
    name: str
    level: Literal["track", "scene"]
    value: Literal["bool", "number", "text"]
    description: str
    needs: frozenset[str]
    fn: Fn
    tier: Tier = "A"
    caveat: str = ""
    own_hold: bool = False  # reads params.hold_s itself; otherwise the engine holds the condition
    replay: bool = False  # can be replayed from the event index by a backtest
    label: str = ""  # plain words for it, when a backtest has to assume it
    zone: bool = False  # reads one zone: a condition without one is never true
    params: tuple[str, ...] = ()  # params a condition must give, or it is never true
    words: tuple[str, ...] = ()  # every value a words signal can give, besides unknown


REGISTRY: dict[str, SignalSpec] = {}


def signal(name: str, level: Literal["track", "scene"], value: Literal["bool", "number", "text"],
           description: str, needs: tuple[str, ...] = (), tier: Tier = "A", caveat: str = "",
           own_hold: bool = False, replay: bool = False, label: str = "", zone: bool = False,
           params: tuple[str, ...] = (), words: tuple[str, ...] = ()):
    def register(fn: Fn) -> Fn:
        REGISTRY[name] = SignalSpec(name, level, value, description, frozenset(needs), fn, tier, caveat, own_hold,
                                    replay, label or name.replace("_s", "").replace("_", " "), zone, tuple(params),
                                    tuple(words))
        return fn
    return register


OPS = {"text": ("is", "==", "!=", "contains", "in"), "number": (">=", "<=", "==", "is", "!="),
       "bool": (">=", "==", "is", "!=")}  # the comparisons that can be true for each kind of value


def catalog() -> list[dict]:
    return [{"signal": s.name, "level": s.level, "value": s.value, "description": s.description, "tier": s.tier,
             "ops": list(OPS[s.value]), **({"needs_zone": True} if s.zone else {}),
             **({"needs_params": list(s.params)} if s.params else {}),
             **({"values": list(s.words)} if s.words else {}),
             **({"caveat": s.caveat} if s.caveat else {})}
            for s in REGISTRY.values() if not s.name.startswith("_")]


def worse(a: str, b: str) -> str:
    return max(a, b, key=TIERS.index)


def duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} minutes"


def _in(track: TrackState, zones: list[str]) -> list[str]:
    return [z for z in (zones or list(track.zones_inside)) if z in track.zones_inside]


@signal("present", "track", "bool", "The subject is in view (inside the watch zones when the watch has zones).",
        replay=True)
def present(ctx, cond, track):
    return SignalValue(True, "seen")


@signal("entered", "track", "bool", "The subject came into one of the zones on this moment.", replay=True)
def entered(ctx, cond, track):
    hits = [e.zone for e in ctx.events("enter", track.track_id) if not cond.zones or e.zone in cond.zones]
    return SignalValue(bool(hits), f"came into {hits[0]}" if hits else None)


@signal("exited", "track", "bool", "The subject left one of the zones on this moment.", replay=True)
def exited(ctx, cond, track):
    hits = [e.zone for e in ctx.events("exit", track.track_id) if not cond.zones or e.zone in cond.zones]
    return SignalValue(bool(hits), f"left {hits[0]}" if hits else None)


@signal("in_zone", "track", "bool", "The subject is inside one of the zones.", replay=True)
def in_zone(ctx, cond, track):
    inside = _in(track, cond.zones)
    return SignalValue(bool(inside), f"in {inside[0]}" if inside else None)


@signal("dwell_s", "track", "number", "Seconds the subject has stayed inside one of the zones without leaving.",
        replay=True)
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


@signal("posture", "track", "text", "A person's posture: upright, falling or fallen.", needs=("posture",),
        tier="B", caveat="From body pose; bending down or lying on purpose can look like a fall.", replay=True,
        words=("upright", "falling", "fallen"))
def posture(ctx, cond, track):
    return SignalValue(track.posture, None if track.posture == "upright" else f"posture {track.posture}")


@signal("count", "scene", "number", "How many subjects are inside the zones (or in view when no zones).",
        replay=True)
def count(ctx, cond, track):
    n = sum(1 for t in ctx.subject_tracks if not cond.zones or _in(t, cond.zones))
    where = f" in {cond.zones[0]}" if cond.zones else ""
    return SignalValue(float(n), f"{n} seen{where}")


L_WRIST, R_WRIST, NOSE = 15, 16, 0
VISIBLE = 0.5


@signal("zones_visited", "track", "number",
        "How many different zones the subject has been in within params.window_s seconds (default 300).",
        replay=True)
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
        needs=("pose",), tier="B", caveat="From body pose; hidden hands are missed.")
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
        needs=("pose",), tier="B", caveat="From body pose; stretching up can look like climbing.")
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
        "counting this visit.", tier="B", caveat="Judged by clothes, so people dressed alike are confused.",
        replay=True)
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
        "The view inside a zone (the whole picture when the condition names no zone) matches params.text better "
        "than params.not_text for params.hold_s seconds (open vocabulary, for example 'an open gate' against 'a "
        "closed gate', 'smoke' against 'clear air').",
        needs=("embed",), tier="B", own_hold=True, label="the zone's look", params=("text",),
        caveat="An image and text model judges the look of the zone; the AI double checks each alert.")
def zone_looks_like(ctx, cond, track):
    import numpy as np
    text, not_text = cond.params.get("text"), cond.params.get("not_text")
    where = cond.zones[0] if cond.zones else None
    if ctx.embedder is None or ctx.image is None or not text or (where and where not in ctx.zones):
        return SignalValue(False)
    st = ctx.state.setdefault(f"zone_looks_like:{where or '*'}:{text}", {})
    now = ctx.tick.ts
    if "text" not in st:
        st["text"], st["not"] = ctx.embedder.text(text), ctx.embedder.text(not_text) if not_text else None
    if now - st.get("checked", float("-inf")) >= ctx.tuning.zone_embed_every_s:
        st["checked"] = now
        crop = ctx.image
        if where:
            pts = ctx.zones[where].contour(ctx.tick.size).astype(np.int32)
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
    return SignalValue(ok, f"{where or 'the picture'} looks like {text} for {now - since:.0f} s" if ok else None)


def _text_vec(ctx, text: str):
    cache = ctx.state.setdefault("texts", {})
    if text not in cache:
        cache[text] = ctx.embedder.text(text)
    return cache[text]


@signal("looks_like", "track", "bool",
        "Open vocabulary look or action of the subject, checked locally by an image and text model with no AI cost: "
        "closer to params.text than to params.not_text for params.hold_s seconds (for example 'a person eating' "
        "against 'a person not eating', or glasses, helmets, vests, masks, phones, carried things, clothes).",
        needs=("embed",), tier="B", own_hold=True, label="the look", params=("text",),
        caveat="An image and text model judges looks; the AI double checks each alert.")
def looks_like(ctx, cond, track):
    import numpy as np
    text, not_text = cond.params.get("text"), cond.params.get("not_text")
    if ctx.embedder is None or track.embedding is None or not text:
        return SignalValue(False)
    v = np.asarray(track.embedding, np.float32)
    score = float(np.dot(v, _text_vec(ctx, text)))
    if not_text:
        now = score - float(np.dot(v, _text_vec(ctx, not_text))) >= ctx.tuning.contrast_margin
    else:
        now = score >= ctx.tuning.looks_like_threshold
    since = ctx.state.setdefault("looks_like_since", {})
    key = (text, track.track_id)
    if not now:
        since.pop(key, None)
        return SignalValue(False)
    start = since.setdefault(key, ctx.tick.ts)
    held = ctx.tick.ts - start
    ok = held >= float(cond.params.get("hold_s", 0))
    return SignalValue(ok, f"looks like {text} for {held:.0f} s" if ok else None)


@signal("crossed", "track", "bool",
        "The subject crossed the line named params.line on this moment, going params.direction: 'in', 'out' or "
        "'any' (default). A line's inside is on the right of its first point to its second. Any class, any line.",
        tier="A", caveat="Counted where the feet cross the line; people walking close together can merge.",
        replay=True)
def crossed(ctx, cond, track):
    line = cond.params.get("line") or (cond.zones[0] if cond.zones else None)
    want = cond.params.get("direction") or "any"
    hits = [e for e in ctx.events("cross", track.track_id)
            if (not line or e.zone == line) and want in ("any", e.data.get("direction"))]
    if not hits:
        return SignalValue(False)
    return SignalValue(True, f"crossed {hits[0].zone} going {hits[0].data.get('direction')}")


@signal("stationary_s", "track", "number",
        "Seconds the subject has stayed in the same spot (its box barely moves), carried over short detector misses "
        "and checked against a picture of the spot every few seconds even when nothing moves. For parked or left "
        "behind things, or someone waiting.", needs=("slow",), tier="A", label="staying in one spot",
        caveat="Something hidden behind another thing is missed.")
def stationary_s(ctx, cond, track):
    if cond.zones and not _in(track, cond.zones):
        return SignalValue(0.0)
    h = ctx.history.get(track.track_id)
    if not h or h.anchor is None:
        return SignalValue(0.0)
    seconds = ctx.tick.ts - h.anchor[0]
    return SignalValue(seconds, f"has stayed in one spot for {duration(seconds)}")


def _flow_region(box, pad: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = box
    W, H = size
    return (max(0, int(x - pad * w)), max(0, int(y - pad * h)), min(W, int(x + w + pad * w)),
            min(H, int(y + h + pad * h)))


def _small_gray(image, region, width: int):
    x0, y0, x1, y1 = region
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None, 1.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    scale = width / max(gray.shape[1], 1)
    return cv2.resize(gray, (width, max(8, round(gray.shape[0] * scale))), interpolation=cv2.INTER_AREA), scale


def _own_motion(ctx, prev, cur, region, box, scale: float, percentile: float) -> tuple[float, np.ndarray]:
    """How much the inside of the box moves on its own, in frame pixels: each pixel's DIS flow is compared with
    standing still and with the flow of the ring around the box (water, ground, a lighting change), whichever is
    nearer. Also the ring's flow, so the box's own drift can be judged the same way."""
    dis = ctx.shared.get("dis")
    if dis is None:
        preset = getattr(cv2, f"DISOPTICAL_FLOW_PRESET_{ctx.tuning.still_preset.upper()}")
        dis = ctx.shared["dis"] = cv2.DISOpticalFlow_create(preset)
    flow = dis.calc(prev, cur, None)
    x, y, w, h = box
    inner = np.zeros(flow.shape[:2], bool)
    bx0, by0 = max(0, int((x - region[0]) * scale)), max(0, int((y - region[1]) * scale))
    bx1, by1 = int((x + w - region[0]) * scale), int((y + h - region[1]) * scale)
    inner[by0:max(by1, by0 + 1), bx0:max(bx1, bx0 + 1)] = True
    ring = flow[~inner]
    around = np.median(ring, axis=0) if len(ring) else np.zeros(2, np.float32)
    inside = flow[inner] if inner.any() else flow.reshape(-1, 2)
    residual = np.minimum(np.linalg.norm(inside, axis=1), np.linalg.norm(inside - around, axis=1))
    return float(np.percentile(residual, percentile)) / scale, around / scale


def _update_still(ctx, track, h: TrackHistory) -> None:
    """Per track and once per look, whatever zones the watches name, so watches never reset each other."""
    cfg, st, now = ctx.tuning, h.still, ctx.tick.ts
    if ctx.image is None or now - st.get("ts", float("-inf")) < cfg.still_every_s:
        return
    box, size = track.box, ctx.tick.size
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    prev, dt = st.get("gray"), now - st.get("ts", now)
    still = False
    if prev is not None and 0 < dt <= cfg.still_gap_s:
        cur, scale = _small_gray(ctx.image, st["region"], cfg.still_width)
        if cur is not None and cur.shape == prev.shape:
            body = max(box[3], 1.0)
            own, around = _own_motion(ctx, prev, cur, st["region"], st["box"], scale, cfg.still_percentile)
            moved = np.array([cx - st["centre"][0], cy - st["centre"][1]])
            drift = min(np.linalg.norm(moved), np.linalg.norm(moved - around)) / body / dt
            still = own / body / dt <= cfg.still_flow and drift <= cfg.still_drift
    st["since"] = (st.get("since") or st["ts"]) if still else None
    region = _flow_region(box, cfg.still_pad, size)
    st.update(region=region, box=box, gray=_small_gray(ctx.image, region, cfg.still_width)[0], ts=now,
              centre=(cx, cy))


@signal("still_s", "track", "number",
        "Seconds the subject has shown almost no movement of its own inside its box (dense optical flow, judged "
        "against the flow around it, so drifting with water or a lighting change is not movement), while inside the "
        "zones. For someone or something that should be moving.", tier="B", label="staying still",
        caveat="Resting or floating looks the same as not moving; glare can hide stillness.")
def still_s(ctx, cond, track):
    h = ctx.history.get(track.track_id)
    if h is None:
        return SignalValue(0.0)
    _update_still(ctx, track, h)
    since = h.still.get("since")
    if since is None:
        return SignalValue(0.0)
    if cond.zones:
        inside = _in(track, cond.zones)
        if not inside:
            return SignalValue(0.0)
        since = max(since, min(track.zones_inside[z] for z in inside))
    seconds = ctx.tick.ts - since
    return SignalValue(seconds, f"has hardly moved for {duration(seconds)}" if seconds else None)


def _vanish_hold(ctx, zone: str) -> float:
    kind = ctx.zones[zone].kind if zone in ctx.zones else "area"
    return ctx.tuning.vanish_hold_by_kind.get(kind, ctx.tuning.vanish_hold_s)


@signal("vanished_in_zone", "scene", "bool",
        "Something of the subject's class went out of sight inside one of the zones, away from the picture's edges, "
        "and nothing came back near that spot for params.hold_s seconds (the default depends on the zone kind). "
        "For someone going under water, or into a place they should not stay hidden.", tier="B", own_hold=True,
        caveat="Going behind something or swimming under water looks the same.", replay=True)
def vanished_in_zone(ctx, cond, track):
    zones = cond.zones or [n for n, z in ctx.zones.items() if not z.is_line]
    pending = ctx.state.setdefault("vanished:" + ",".join(zones), [])
    cfg, (W, H), now = ctx.tuning, ctx.tick.size, ctx.tick.ts
    wanted = set(ctx.classes)
    for s in ctx.tick.ended:
        inside = [z for z in zones if z in s.zones_inside]
        if not inside or (wanted and s.cls not in wanted):
            continue
        x, y, w, h = s.box
        e = cfg.vanish_edge
        if x <= e * W or y <= e * H or x + w >= (1 - e) * W or y + h >= (1 - e) * H:
            continue  # walked out of the picture
        pending.append({"ts": s.last_ts, "point": foot_point(s.box), "h": h, "zone": inside[0]})
    gone = None
    for p in list(pending):
        back = any(t.first_ts >= p["ts"] and (not wanted or t.cls in wanted)
                   and math.dist(foot_point(t.box), p["point"]) <= cfg.vanish_near * max(p["h"], t.box[3], 1.0)
                   for t in ctx.tick.tracks)
        hold = float(cond.params.get("hold_s") or _vanish_hold(ctx, p["zone"]))
        if back:
            pending.remove(p)
        elif now - p["ts"] >= hold:
            pending.remove(p)
            gone = gone or p
    if gone is None:
        return SignalValue(False)
    return SignalValue(True, f"went out of sight in {gone['zone']} {now - gone['ts']:.0f} s ago and has not come back")


def _company(me, other, cfg) -> bool:
    """Right next to each other on screen is company whatever the box sizes (a swimmer's head and shoulders beside a
    standing lifeguard). Further apart, box heights far apart mean different depths, so not company."""
    h1, h2 = max(me.box[3], 1.0), max(other.box[3], 1.0)
    gap = math.dist(foot_point(me.box), foot_point(other.box))
    if gap <= cfg.alone_distance * min(h1, h2):
        return True
    if max(h1, h2) / min(h1, h2) > cfg.alone_depth_ratio:
        return False
    return gap <= cfg.alone_distance * (h1 + h2) / 2


@signal("alone", "track", "bool",
        "The subject is the only one of its class (or of params.classes) inside the zones, and no other is within a "
        "few body heights of it at the same depth.", tier="A", replay=True)
def alone(ctx, cond, track):
    where = _in(track, cond.zones) if cond.zones else []
    if cond.zones and not where:
        return SignalValue(False)
    company = set(cond.params.get("classes") or [track.cls])
    for t in ctx.tick.tracks:
        if t.track_id == track.track_id or t.cls not in company:
            continue
        if any(z in t.zones_inside for z in cond.zones) or _company(track, t, ctx.tuning):
            return SignalValue(False)
    return SignalValue(True, f"alone in {where[0]}" if where else "alone")


@signal("zone_state", "scene", "text",
        "The state of a zone, learned from example pictures the owner saved for it (any state names, for example "
        "'open' and 'closed', 'covered' and 'uncovered', 'empty' and 'occupied'), checked every few seconds even "
        "when nothing moves; 'unknown' until there are examples. Use op 'is' with the state name as value.",
        needs=("embedder",), tier="B", label="the zone's state", zone=True,
        caveat="Needs a few example pictures of each state, by day and by night.")
def zone_state(ctx, cond, track):
    if not cond.zones or ctx.states is None or not ctx.states.has(cond.zones[0]) or cond.zones[0] not in ctx.zones:
        return SignalValue("unknown")
    zone, cfg, now = cond.zones[0], ctx.states.cfg, ctx.tick.ts
    mem = ctx.shared.setdefault("zone_state", {}).setdefault(zone, {})  # one reading per zone for every watch
    if ctx.health != "live":
        mem.clear()  # nothing is known about the zone while the camera can't see it
        return SignalValue("unknown")
    if ctx.image is not None and now - mem.get("checked", float("-inf")) >= cfg.slow_tick_s:
        mem["checked"] = now
        guess = ctx.states.classify(ctx.zones[zone], ctx.image)
        if guess.state == "unsure":
            mem["unsure"] = mem.get("unsure", 0) + 1
        else:
            mem.update(state=guess.state, sure=now, unsure=0)
    if mem.get("unsure", 0) >= cfg.state_unsure_checks or now - mem.get("sure", float("-inf")) > cfg.state_max_age_s \
            or now - mem.get("checked", float("-inf")) > 2 * cfg.slow_tick_s:
        mem.pop("state", None)
    state = mem.get("state", "unknown")
    return SignalValue(state, f"{zone} is {state}" if state != "unknown" else None)


@signal("attribute", "track", "text",
        "A value a reader attached to the subject, named by params.name (for example plate_text or plate_colour from "
        "the number plate reader); 'unknown' until read. Compare with op 'is', '!=', 'contains', or 'in' with a list.",
        tier="B", label="what the reader read", params=("name",),
        caveat="Only as good as the reader; colours can't be told apart at night on black and white cameras.")
def attribute(ctx, cond, track):
    name = str(cond.params.get("name") or "")
    value = track.attributes.get(name) if name else None
    return SignalValue(value or "unknown", f"{name.replace('_', ' ')} {value}" if value else None)


@signal("inside_s", "track", "number",
        "Seconds since the subject came in over a line (going in) on a camera with plate reading, without going out "
        "again, on any camera of the society. A vehicle is followed by its number plate after it leaves view, so a "
        "watch like 'a cab stays more than 2 hours' is attribute plate_colour is yellow with inside_s at least 7200. "
        "Combine it only with attribute; the gate log checks it every minute.",
        tier="B", label="inside the society",
        caveat="Only vehicles whose plate was read going in are followed; an unread plate is not.")
def inside_s(ctx, cond, track):
    try:
        seconds = float(track.attributes.get("inside_s") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    return SignalValue(seconds, f"inside for {duration(seconds)}" if seconds else None)


@signal("camera_health", "scene", "text",
        "The camera's own state: live, covered, blurred, moved or offline. Not an event in the picture.", replay=True,
        words=("live", "covered", "blurred", "moved", "offline"))
def camera_health(ctx, cond, track):
    return SignalValue(ctx.health, None if ctx.health == "live" else f"camera {ctx.health}")


@signal("_assume", "scene", "bool", "Stands in for a condition a backtest cannot replay.", replay=True)
def _assume(ctx, cond, track):
    return SignalValue(bool(cond.params.get("value")))
