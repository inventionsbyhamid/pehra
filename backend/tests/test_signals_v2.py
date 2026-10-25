from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from watchful.rules.engine import RuleEngine
from watchful.rules.watch import Condition, Subject, Watch
from watchful.rules.zonestate import ZoneStates
from watchful.store.db import Camera, EventRow
from watchful.tuning import Tuning
from watchful.vision.perceive import Observation, Perception
from watchful.vision.reason import Reasoner, Tick, TrackState
from watchful.vision.zones import Zone

TZ = "Asia/Kolkata"


def at(hh, mm=0):
    return datetime(2026, 10, 23, hh, mm, tzinfo=ZoneInfo(TZ)).timestamp()


def watch(*conditions, classes=("person",), **kw):
    return Watch(name="w", subject=Subject(classes=list(classes)), conditions=list(conditions), verify="?",
                 cooldown_s=kw.pop("cooldown_s", 1), **kw)


def engine(w, zones, **kw):
    return RuleEngine([("w", w)], zones, Tuning(), TZ, **kw)


def obs(ts, tid, x, y=200.0, w=50.0, h=150.0, cls="person"):
    return Observation(ts, tid, cls, (x - w / 2, y - h, w, h), 0.9)


# crossing a directed line

LINE_SIZE = (1000, 500)
# drawn bottom to top, so its inside (the right of first point to second point) is the right of the screen
GATE = Zone("l1", "gate", [(0.5, 1.0), (0.5, 0.0)], "line")
SHORT = Zone("l2", "lower gate", [(0.3, 1.0), (0.3, 0.6)], "line")


def walk(reasoner, path, tid=1, t0=0.0, step=0.5, y=400.0):
    ticks = []
    for i, x in enumerate(path):
        ts = t0 + i * step
        ticks.append(reasoner.step(Perception(ts, LINE_SIZE, True, [obs(ts, tid, x, y)])))
    return ticks


def crossings(ticks):
    return [(e.zone, e.data["direction"]) for t in ticks for e in t.events if e.kind == "cross"]


def test_crossing_in_and_out_by_direction():
    r = Reasoner(Camera("cam", "Gate", "stream", None, [GATE]), Tuning())
    ticks = walk(r, [100, 300, 450, 560, 700, 900])
    assert crossings(ticks) == [("gate", "in")]
    back = walk(r, [900, 700, 520, 440, 200], t0=10)
    assert crossings(back) == [("gate", "out")]


def test_jitter_on_the_line_counts_once():
    r = Reasoner(Camera("cam", "Gate", "stream", None, [GATE]), Tuning())
    ticks = walk(r, [300, 600, 495, 505, 492, 508, 497, 503, 700])
    assert crossings(ticks) == [("gate", "in")]


def test_crossing_beyond_the_end_of_a_line_does_not_count():
    r = Reasoner(Camera("cam", "Gate", "stream", None, [SHORT]), Tuning())
    assert crossings(walk(r, [100, 250, 350, 500], y=150.0)) == []
    assert crossings(walk(r, [100, 250, 350, 500], tid=2, t0=20, y=450.0)) == [("lower gate", "in")]


def test_crossed_signal_follows_direction_for_any_class():
    r = Reasoner(Camera("cam", "Gate", "stream", None, [GATE]), Tuning())
    ticks = walk(r, [100, 300, 560, 700]) + walk(r, [700, 400, 200], t0=10)
    w_in = watch(Condition(signal="crossed", params={"line": "gate", "direction": "in"}))
    w_any = watch(Condition(signal="crossed", params={"line": "gate", "direction": "any"}))
    e_in, e_any = engine(w_in, [GATE]), engine(w_any, [GATE])
    fired_in = [c for t in ticks for c in e_in.on_tick("cam", t, t.ts)]
    fired_any = [c for t in ticks for c in e_any.on_tick("cam", t, t.ts)]
    assert len(fired_in) == 1 and len(fired_any) == 2
    assert any("gate" in r for r in fired_in[0].evidence["reasons"])


# parked, still, vanished, alone

PARK = (300.0, 200.0, 200.0, 100.0)
PARK_ZONE = Zone("z1", "fire lane", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])


def car(tid, first, box=PARK, ts=None):
    return TrackState(tid, "car", box, first, ts if ts is not None else first, zones_inside={"fire lane": first})


def parked_watch():
    return watch(Condition(signal="stationary_s", zones=["fire lane"], op=">=", value=600), classes=("car",),
                 cooldown_s=3600)


def test_a_parked_box_is_stationary_for_600_s():
    t0 = at(12)
    e = engine(parked_watch(), [PARK_ZONE])
    fired = [ts for ts in range(0, 660, 5)
             if e.on_tick("cam", Tick(t0 + ts, (1000, 500), False, [car(1, t0)], []), t0 + ts)]
    assert len(fired) == 1 and 600 <= fired[0] <= 610


def test_a_moving_car_is_not_stationary():
    t0 = at(12)
    e = engine(parked_watch(), [PARK_ZONE])
    fired = []
    for ts in range(0, 660, 5):
        box = (PARK[0] + (ts % 60) * 3, *PARK[1:])
        fired += e.on_tick("cam", Tick(t0 + ts, (1000, 500), True, [car(1, t0, box)], []), t0 + ts)
    assert fired == []


def test_parked_time_survives_a_detector_miss():
    t0 = at(12)
    e = engine(parked_watch(), [PARK_ZONE])
    fired = []
    for ts in range(0, 660, 5):
        if ts < 300:
            tracks, ended = [car(1, t0)], []
        elif ts == 300:
            tracks, ended = [], [car(1, t0, ts=t0 + 295)]
        elif ts < 330:
            tracks, ended = [], []
        else:
            tracks, ended = [car(2, t0 + 330)], []
        fired += [ts] if e.on_tick("cam", Tick(t0 + ts, (1000, 500), False, tracks, [], ended), t0 + ts) else []
    assert fired and 600 <= fired[0] <= 610


def scene(car_there=True, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((500, 1000, 3), 110, np.uint8)
    img += rng.integers(0, 6, img.shape, dtype=np.uint8)
    if car_there:
        x, y, w, h = (int(v) for v in PARK)
        cv2.rectangle(img, (x, y), (x + w, y + h), (40, 40, 160), -1)
        cv2.rectangle(img, (x + 20, y + 10), (x + 80, y + 45), (200, 220, 230), -1)
        cv2.rectangle(img, (x + 110, y + 10), (x + 180, y + 45), (200, 220, 230), -1)
        cv2.circle(img, (x + 40, y + h), 20, (20, 20, 20), -1)
        cv2.circle(img, (x + 160, y + h), 20, (20, 20, 20), -1)
    return img


def test_a_long_detector_gap_is_bridged_by_the_parked_picture():
    t0 = at(12)

    def run(car_seen_in_gap: bool):
        e = engine(parked_watch(), [PARK_ZONE])
        fired = []
        for ts in range(0, 700, 5):
            image = scene(car_there=ts < 300 or ts >= 500 or car_seen_in_gap, seed=ts)
            if ts < 300:
                tracks, ended = [car(1, t0)], []
            elif ts == 300:
                tracks, ended = [], [car(1, t0, ts=t0 + 295)]
            elif ts < 500:
                tracks, ended = [], []
            else:
                tracks, ended = [car(2, t0 + 500)], []
            if e.on_tick("cam", Tick(t0 + ts, (1000, 500), False, tracks, [], ended), t0 + ts, image):
                fired.append(ts)
        return fired

    bridged = run(True)
    assert bridged and 600 <= bridged[0] <= 610
    assert run(False) == []  # the car really left during the gap, so the clock starts again


POOL_SIZE = (320, 240)
WATER = Zone("w1", "water", [(0.1, 0.2), (0.9, 0.2), (0.9, 0.9), (0.1, 0.9)], "water")
DECK = Zone("d1", "deck", [(0.0, 0.0), (1.0, 0.0), (1.0, 0.2), (0.0, 0.2)])
SWIMMER = (140.0, 90.0, 40.0, 60.0)


def pool_frames(moving: bool, seconds=30, step=0.5):
    rng = np.random.default_rng(1)
    texture = cv2.GaussianBlur(rng.integers(0, 255, (60, 40, 3)).astype(np.uint8), (0, 0), 2)
    base = np.zeros((240, 320, 3), np.uint8)
    base[:] = (170, 120, 40)
    frames = []
    for i in range(int(seconds / step)):
        img = base.copy()
        patch = np.roll(texture, 3 * i, axis=0) if moving else texture
        img[90:150, 140:180] = patch
        noise = rng.normal(0, 2, img.shape)
        frames.append((i * step, np.clip(img + noise, 0, 255).astype(np.uint8)))
    return frames


def swimmer(t0, first=None):
    return TrackState(7, "person", SWIMMER, first or t0, t0, zones_inside={"water": first or t0})


def test_a_still_person_in_the_water():
    t0 = at(12)
    w = watch(Condition(signal="in_zone", zones=["water"]),
              Condition(signal="still_s", zones=["water"], op=">=", value=20), cooldown_s=600)

    def fired(moving):
        e = engine(w, [WATER, DECK])
        out = []
        for ts, img in pool_frames(moving):
            if e.on_tick("cam", Tick(t0 + ts, POOL_SIZE, True, [swimmer(t0)], []), t0 + ts, img):
                out.append(ts)
        return out

    still = fired(False)
    assert len(still) == 1 and 20 <= still[0] <= 22
    assert fired(True) == []


def vanished_watch(zone="water", **params):
    return watch(Condition(signal="vanished_in_zone", zones=[zone], params=params), cooldown_s=600)


def ended_in(zone, t_end, box=SWIMMER):
    return TrackState(7, "person", box, t_end - 20, t_end, zones_inside={zone: t_end - 20})


def run_ticks(e, t0, seconds, extra=lambda ts: ([], []), step=1.0):
    fired = []
    for i in range(int(seconds / step)):
        ts = i * step
        tracks, ended = extra(ts)
        if e.on_tick("cam", Tick(t0 + ts, POOL_SIZE, True, tracks, [], ended), t0 + ts):
            fired.append(ts)
    return fired


def test_a_track_vanishing_in_water_for_15_s():
    t0 = at(12)
    e = engine(vanished_watch(), [WATER, DECK])
    fired = run_ticks(e, t0, 40, lambda ts: ([], [ended_in("water", t0 + 10)]) if ts == 10 else ([], []))
    assert fired == [25]


def test_someone_coming_back_near_the_spot_cancels_the_vanish():
    t0 = at(12)
    e = engine(vanished_watch(), [WATER, DECK])

    def extra(ts):
        if ts == 10:
            return [], [ended_in("water", t0 + 10)]
        if ts >= 16:
            return [TrackState(8, "person", (145.0, 92.0, 40.0, 60.0), t0 + 16, t0 + ts,
                               zones_inside={"water": t0 + 16})], []
        return [], []

    assert run_ticks(e, t0, 40, extra) == []


def test_leaving_the_picture_at_the_edge_is_not_a_vanish():
    t0 = at(12)
    edge = (2.0, 90.0, 40.0, 60.0)
    zone = Zone("w2", "water", [(0.0, 0.2), (0.9, 0.2), (0.9, 0.9), (0.0, 0.9)], "water")
    e = engine(vanished_watch(), [zone, DECK])
    assert run_ticks(e, t0, 40, lambda ts: ([], [ended_in("water", t0 + 10, edge)]) if ts == 10 else ([], [])) == []


def test_vanish_works_in_any_zone_with_the_kind_default_from_tuning():
    t0 = at(12)
    store = Zone("s1", "store room", WATER.polygon)
    e = engine(vanished_watch("store room"), [store])
    fired = run_ticks(e, t0, 60, lambda ts: ([], [ended_in("store room", t0 + 10)]) if ts == 10 else ([], []))
    assert fired == [10 + Tuning().signals.vanish_hold_s]
    e = engine(vanished_watch("store room", hold_s=5), [store])
    assert run_ticks(e, t0, 60, lambda ts: ([], [ended_in("store room", t0 + 10)]) if ts == 10 else ([], [])) == [15]


def test_alone_versus_two_people():
    t0 = at(21)
    w = watch(Condition(signal="alone", zones=["water"]), cooldown_s=600)
    one = TrackState(1, "person", SWIMMER, t0, t0, zones_inside={"water": t0})
    far = TrackState(2, "person", (0.0, 0.0, 10.0, 20.0), t0, t0, zones_inside={})
    near = TrackState(3, "person", (200.0, 90.0, 40.0, 60.0), t0, t0, zones_inside={})
    other_in = TrackState(4, "person", (250.0, 180.0, 40.0, 60.0), t0, t0, zones_inside={"water": t0})
    a_dog = TrackState(5, "dog", (190.0, 95.0, 30.0, 40.0), t0, t0, zones_inside={"water": t0})
    for tracks, alone in (([one], True), ([one, far], True), ([one, a_dog], True), ([one, near], False),
                          ([one, other_in], False)):
        got = engine(w, [WATER, DECK]).on_tick("cam", Tick(t0, POOL_SIZE, True, tracks, []), t0)
        assert bool([c for c in got if c.track_id == 1]) == alone, tracks


# zone state by example

GATE_ZONE = Zone("g1", "gate", [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)])


def gate_frame(state: str, night: bool, seed: int, shift: int = 0):
    rng = np.random.default_rng(seed)
    img = np.zeros((240, 320, 3), np.uint8)
    img[:] = (60, 140, 90) if not night else (70, 70, 70)
    img[180:, :] = (90, 90, 120) if not night else (55, 55, 55)
    bar = (30, 60, 200) if not night else (150, 150, 150)
    xs = range(85, 235, 18) if state == "closed" else range(82, 112, 6)
    for x in xs:
        cv2.rectangle(img, (x + shift, 62), (x + shift + 7, 178), bar, -1)
    cv2.line(img, (80 + shift, 70), (240 + shift, 70), bar, 3)
    noise = rng.normal(0, 4 if not night else 7, img.shape)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def gate_states():
    states = ZoneStates(Tuning().zones)
    for night in (False, True):
        for state in ("open", "closed"):
            states.add(GATE_ZONE, state, gate_frame(state, night, seed=1))
    return states


def test_zone_state_open_and_closed_by_example_day_and_night():
    states = gate_states()
    for night in (False, True):
        for state in ("open", "closed"):
            g = states.classify(GATE_ZONE, gate_frame(state, night, seed=7, shift=2))
            assert (g.state, g.period) == (state, "night" if night else "day")


def test_zone_state_watch_runs_on_the_slow_tier_without_motion():
    states = gate_states()
    calls = []
    classify = states.classify
    states.classify = lambda zone, image: calls.append(1) or classify(zone, image)
    w = watch(Condition(signal="zone_state", zones=["gate"], op="is", value="open", params={"hold_s": 120}),
              classes=(), cooldown_s=3600)
    e = engine(w, [GATE_ZONE], states=states)
    t0 = at(23, 30)
    fired = []
    for i in range(200):
        img = gate_frame("open" if i >= 20 else "closed", night=True, seed=i)
        if e.on_tick("cam", Tick(t0 + i, (320, 240), False, [], []), t0 + i, img):
            fired.append(i)
    slow = Tuning().zones.slow_tick_s
    assert len(fired) == 1 and 140 <= fired[0] <= 140 + 2 * slow
    assert len(calls) <= 200 / slow + 1


def test_zone_state_without_examples_is_unknown():
    w = watch(Condition(signal="zone_state", zones=["gate"], op="is", value="open"), classes=())
    e = engine(w, [GATE_ZONE])
    assert e.on_tick("cam", Tick(at(12), (320, 240), False, [], []), at(12), gate_frame("open", False, 1)) == []


# attributes, camera health, holds

def test_attribute_signal_reads_whatever_a_reader_attached():
    t0 = at(12)
    cab = TrackState(1, "car", PARK, t0, t0, attributes={"plate_colour": "yellow", "plate_text": "MH12AB1234"})
    plain = TrackState(2, "car", PARK, t0, t0)
    cases = [(Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"), [1]),
             (Condition(signal="attribute", params={"name": "plate_colour"}, op="in", value=["green", "yellow"]), [1]),
             (Condition(signal="attribute", params={"name": "plate_text"}, op="contains", value="mh12"), [1]),
             (Condition(signal="attribute", params={"name": "plate_colour"}, op="!=", value="yellow"), [2])]
    for cond, ids in cases:
        tick = Tick(t0, (1000, 500), True, [cab, plain], [])
        got = engine(watch(cond, classes=("car",)), []).on_tick("cam", tick, t0)
        assert [c.track_id for c in got] == ids, cond


def tamper(ts, kind):
    return EventRow(None, "cam", ts, f"tamper_{kind}", None, None, None, {}, None, None, None, None)


def test_camera_health_follows_tamper_events_and_offline():
    t0 = at(12)
    w = watch(Condition(signal="camera_health", op="!=", value="live"), classes=(), cooldown_s=1)
    e = engine(w, [])
    seq = [Tick(t0, (320, 240), False, [], []), Tick(t0 + 1, (320, 240), False, [], [tamper(t0 + 1, "covered")]),
           Tick(t0 + 2, (320, 240), False, [], []), Tick(t0 + 3, (320, 240), False, [], [tamper(t0 + 3, "restored")]),
           Tick(t0 + 4, (320, 240), False, [], [], health="offline")]
    fired = [t.ts - t0 for t in seq if e.on_tick("cam", t, t.ts)]
    assert fired == [1, 4]


def test_any_signal_can_be_held_for_seconds():
    t0 = at(10)
    chair = Zone("c1", "lifeguard chair", [(0.0, 0.0), (0.2, 0.0), (0.2, 0.3), (0.0, 0.3)])
    w = watch(Condition(signal="count", zones=["lifeguard chair"], op="<=", value=0, params={"hold_s": 30}),
              classes=("person",), cooldown_s=600)
    e = engine(w, [chair])
    guard = TrackState(1, "person", (10.0, 10.0, 20.0, 50.0), t0, t0, zones_inside={"lifeguard chair": t0})
    fired = []
    for ts in range(0, 120):
        away = 20 <= ts < 30 or ts >= 60  # a short step away, then gone
        if e.on_tick("cam", Tick(t0 + ts, POOL_SIZE, True, [] if away else [guard], []), t0 + ts):
            fired.append(ts)
    assert fired == [90]


def test_slow_tier_runs_the_detector_without_motion(tmp_path):
    from watchful.config import Settings
    from watchful.ingest.sources import Frame
    from watchful.store.blobs import LocalBlobStore
    from watchful.vision.perceive import Needs, Perceiver

    class Stub:
        name = "stub"
        calls = 0

        def set_classes(self, classes):
            pass

        def detect(self, image):
            self.calls += 1
            return []

    settings = Settings(demo_password="x", session_secret="y", _env_file=None)
    img = np.full((240, 320, 3), 90, np.uint8)
    counts = {}
    for slow in (False, True):
        det = Stub()
        p = Perceiver(Camera("cam", "c", "stream", None), settings, Tuning(), LocalBlobStore(tmp_path), None,
                      Needs(classes={"car"}, slow=slow), detector=det)
        for i in range(60):
            p.perceive(Frame(img.copy(), i * 0.5, i))
        counts[slow] = det.calls
    assert counts[True] >= 30 / Tuning().zones.slow_tick_s and counts[True] - counts[False] >= 4


def test_state_examples_are_kept_per_society(tmp_path):
    import pytest
    from watchful.db.session import make_engine, migrate
    from watchful.rules.zonestate import example_key, load_states
    from watchful.store.sql import SqlRepository, create_workspace

    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    migrate(engine)
    a = SqlRepository(engine, create_workspace(engine, "A").id)
    b = SqlRepository(engine, create_workspace(engine, "B").id)
    cam = a.add_camera(Camera(None, "Gate", "stream", None, [GATE_ZONE]))
    pictures = {}
    for night in (False, True):
        for state in ("open", "closed"):
            key = example_key(cam.id, "g1", "night" if night else "day", len(pictures))
            pictures[key] = cv2.imencode(".png", gate_frame(state, night, seed=1))[1].tobytes()
            a.add_state_example(cam.id, "g1", state.title(), key)
    rows = a.list_state_examples(cam.id)
    assert [(z, st) for _, z, st, _ in rows] == [("g1", "open"), ("g1", "closed")] * 2
    assert b.list_state_examples(cam.id) == []
    with pytest.raises(KeyError):
        b.add_state_example(cam.id, "g1", "open", "b/x.jpg")
    states = load_states(rows, pictures.__getitem__, [GATE_ZONE], Tuning().zones)
    assert states.states("gate") == {"open": {"day", "night"}, "closed": {"day", "night"}}
    assert states.classify(GATE_ZONE, gate_frame("closed", True, seed=3)).state == "closed"
    b.delete_state_example(rows[0][0])
    assert len(a.list_state_examples(cam.id)) == 4
    a.delete_state_example(rows[0][0])
    assert len(a.list_state_examples(cam.id)) == 3
