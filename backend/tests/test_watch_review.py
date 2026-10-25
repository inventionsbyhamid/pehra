"""The review probes of 058cebc (P1 to P9 and the minors), as tests."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from watchful.agent.compiler import CompileContext, Invalid, bare_age_name, check_watch, compile_watch, mentions_age
from watchful.agent.llm import FakeLLM
from watchful.rules import signals
from watchful.rules.backtest import backtest
from watchful.rules.engine import RuleEngine
from watchful.rules.watch import Condition, Subject, TimeWindow, Watch, select_watches
from watchful.rules.zonestate import ZoneStates
from watchful.store.db import Camera, EventRow
from watchful.tuning import Tuning
from watchful.vision.perceive import Observation, Perception
from watchful.vision.reason import Reasoner, Tick, TrackState
from watchful.vision.zones import LineTracker, Zone

TZ = "Asia/Kolkata"


def at(hh, mm=0, ss=0, day=23):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=ZoneInfo(TZ)).timestamp()


def W(*conds, classes=("person",), **kw):
    return Watch(name="w", subject=Subject(classes=list(classes)), conditions=list(conds), verify="?",
                 cooldown_s=kw.pop("cooldown_s", 1), **kw)


def reply(watch):
    return json.dumps({"watch": watch, "coverage": [], "setup": [], "cannot": []})


# P1: a place name with an age word in it is a place, not an age

def test_p1_kids_play_area_is_a_place():
    ctx = CompileContext(zones={"kids play area": "area"}, purpose="other")
    ai = reply({"name": "Someone in the kids play area at night", "subject": {"classes": ["person"]},
                "conditions": [{"signal": "in_zone", "zones": ["kids play area"]}],
                "time_window": {"start": "22:00", "end": "06:00"}, "severity": "warning",
                "verify": "Is someone in the kids play area?"})
    r = compile_watch("someone in the kids play area after 10 PM", ctx, FakeLLM([ai]))
    assert r.watch is not None and r.cannot == []
    assert r.watch.name == "Someone in the kids play area at night"
    assert r.watch.verify == "Is someone in the kids play area?"
    assert not mentions_age(json.dumps(r.watch.model_dump()), names=["kids play area"])
    assert mentions_age(json.dumps(r.watch.model_dump()))  # without the place names it would read as age
    kid = reply({"name": "Kid in the kids play area", "subject": {"classes": ["person"]},
                 "conditions": [{"signal": "in_zone", "zones": ["kids play area"]}], "verify": "?"})
    r = compile_watch("a kid in the kids play area", ctx, FakeLLM([kid]))
    assert r.cannot and "kids play area" in r.watch.name


# P2: unknown fails every comparison; a watch on an attribute nobody reads gets a setup task

GATE = Zone("g1", "gate", [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)])


def test_p2_unknown_never_matches():
    w = W(Condition(signal="zone_state", zones=["gate"], op="!=", value="closed"), classes=())
    e = RuleEngine([("w", w)], [GATE], Tuning(), TZ)
    assert e.on_tick("cam", Tick(at(23), (320, 240), False, [], []), at(23), np.full((240, 320, 3), 90, np.uint8)) == []
    car = TrackState(1, "car", (10.0, 10.0, 50.0, 30.0), at(23), at(23))
    for op, value in (("!=", "white"), ("contains", "unk"), ("in", ["unknown", "white"]), ("is", "unknown")):
        w2 = W(Condition(signal="attribute", params={"name": "plate_colour"}, op=op, value=value), classes=("car",))
        e2 = RuleEngine([("w", w2)], [], Tuning(), TZ)
        assert e2.on_tick("cam", Tick(at(23), (320, 240), True, [car], []), at(23)) == [], op


def test_p2_an_attribute_without_a_reader_is_a_setup_task():
    cab = reply({"name": "Cabs", "subject": {"classes": ["car"]}, "output": "log", "verify": "",
                 "conditions": [{"signal": "attribute", "params": {"name": "plate_colour"}, "op": "is",
                                 "value": "yellow"}]})
    plain = CompileContext(lines=["gate"])
    r = compile_watch("log every cab", plain, FakeLLM([cab]))
    assert r.watch is not None and "Turn on plate reading for this camera." in r.setup
    reading = CompileContext(lines=["gate"], camera_flags={"attributes": ["plate_text", "plate_colour"]})
    r = compile_watch("log every cab", reading, FakeLLM([cab]))
    assert not any("plate reading" in s for s in r.setup)


# P3: the backtest says what it assumed, without a direction, and counts visits

WATER = Zone("w1", "water", [(0.1, 0.2), (0.9, 0.2), (0.9, 0.9), (0.1, 0.9)], "water")


class ListRepo:
    def __init__(self, cam, rows):
        self.cam, self.rows = cam, rows

    def get_camera(self, cid):
        return self.cam

    def list_enrollments(self):
        return []

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None, limit=500):
        return [r for r in self.rows if since <= r.ts <= until and (not kinds or r.kind in kinds)][:limit]


def row(t, kind, tid, zone=None, **data):
    return EventRow(None, "cam", t, kind, tid, "person", zone, {"box": [140, 90, 40, 60], **data}, None, None, None,
                    None)


def test_p3_backtest_counts_visits_and_names_what_it_assumed():
    t = at(12)
    rows = [row(t, "track_start", 7, size=[320, 240]), row(t + 1, "enter", 7, "water"),
            row(t + 60, "exit", 7, "water"), row(t + 61, "track_end", 7, zones=[])]
    w = W(Condition(signal="in_zone", zones=["water"]), Condition(signal="still_s", zones=["water"], op=">=", value=20),
          cooldown_s=10)
    r = backtest(w, ListRepo(Camera("cam", "Pool", "stream", None, [WATER]), rows), ["cam"], now=t + 3600, tz=TZ)
    assert r.visits == 1 and r.assumed == ["still_s"]
    assert r.summary.startswith("About 1 visit") and "assuming" in r.summary and "still" in r.summary
    assert not hasattr(r, "upper_bound") and not hasattr(r, "lower_bound")
    exact = backtest(W(Condition(signal="in_zone", zones=["water"])), ListRepo(Camera("cam", "Pool", "stream", None,
                     [WATER]), rows), ["cam"], now=t + 3600, tz=TZ)
    assert exact.assumed == [] and exact.summary == "Would have fired 1 time."


def test_backtest_names_alone_as_approximate():
    t = at(12)
    rows = [row(t, "track_start", 7, size=[320, 240]), row(t + 1, "enter", 7, "water"),
            row(t + 60, "exit", 7, "water"), row(t + 61, "track_end", 7, zones=[])]
    r = backtest(W(Condition(signal="alone", zones=["water"])),
                 ListRepo(Camera("cam", "Pool", "stream", None, [WATER]), rows), ["cam"], now=t + 3600, tz=TZ)
    assert r.fires == 1 and r.approximate == ["alone"] and "approximate" in r.summary


# P4 and P9: still in water, judged against the water around the body, per track

def water_frames(seconds, step=0.5, body_only_static=False, jpeg=None, flicker=0.0, speed=1, drift=False,
                 swimming=False):
    rng = np.random.default_rng(1)
    tex = cv2.GaussianBlur(rng.integers(0, 255, (60, 40, 3)).astype(np.uint8), (0, 0), 2)
    water = cv2.GaussianBlur(rng.integers(0, 255, (240, 640, 3)).astype(np.uint8), (0, 0), 3)
    out = []
    x0 = 60 if drift else 150  # a drifting body starts on the left and floats right with the water
    for i in range(int(seconds / step)):
        if body_only_static or drift:
            img = np.roll(water, speed * i, axis=1).copy()
            x = x0 + (speed * i if drift else 0)
            if swimming:  # a swimmer fills most of the box and moves within it
                img[96:144, 146:174] = np.roll(tex, 3 * i, axis=0)[:48, :28]
            else:
                img[105:135, x:x + 20] = tex[:30, :20]
        else:
            img = np.full((240, 640, 3), (170, 120, 40), np.uint8)
            img[90:150, 140:180] = tex
        img = img.astype(np.float32) * (1 + flicker * ((i % 2) * 2 - 1)) + rng.normal(0, 2, img.shape)
        img = np.clip(img, 0, 255).astype(np.uint8)
        if jpeg:
            img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg])[1], cv2.IMREAD_COLOR)
        out.append((i * step, img, (x0 - 10.0 + (speed * i if drift else 0), 90.0, 40.0, 60.0)))
    return out


def longest_still(frames):
    t0 = at(12)
    w = W(Condition(signal="in_zone", zones=["water"]), Condition(signal="still_s", zones=["water"], op=">=", value=20),
          cooldown_s=600)
    e = RuleEngine([("w", w)], [WATER], Tuning(), TZ)
    fired = []
    for ts, img, box in frames:
        sw = TrackState(7, "person", box, t0, t0 + ts, zones_inside={"water": t0})
        fired += [ts for _ in e.on_tick("cam", Tick(t0 + ts, (640, 240), True, [sw], []), t0 + ts, img)]
    return fired


@pytest.mark.parametrize("label,kw", [
    ("clean", {}), ("jpeg q60", {"jpeg": 60}), ("jpeg q60 and 3% flicker", {"jpeg": 60, "flicker": 0.03}),
    ("still body, water 2 px/s", {"body_only_static": True}),
    ("still body, water 6 px/s", {"body_only_static": True, "speed": 3}),
    ("still body, water 10 px/s", {"body_only_static": True, "speed": 5}),
    ("body drifting with water 6 px/s", {"drift": True, "speed": 3}),
    ("body drifting with water 10 px/s", {"drift": True, "speed": 5})])
def test_p4_a_still_body_is_still_in_moving_water(label, kw):
    fired = longest_still(water_frames(30 if kw.get("drift") else 60, **kw))
    assert fired and 20 <= fired[0] <= 25, label


@pytest.mark.parametrize("speed", [1, 3, 5])
def test_p4_a_swimmer_in_moving_water_is_not_still(speed):
    assert longest_still(water_frames(60, body_only_static=True, speed=speed, swimming=True)) == []


def test_p9_two_still_watches_on_different_zones_do_not_reset_each_other():
    deck = Zone("d1", "deck", [(0.0, 0.0), (1.0, 0.0), (1.0, 0.7), (0.0, 0.7)])
    water = Zone("w2", "water", [(0.0, 0.7), (1.0, 0.7), (1.0, 1.0), (0.0, 1.0)], "water")
    t0 = at(12)
    on_deck = W(Condition(signal="still_s", zones=["deck"], op=">=", value=20), cooldown_s=600)
    in_water = W(Condition(signal="still_s", zones=["water"], op=">=", value=20), cooldown_s=600)
    for watches in ([("deck", on_deck)], [("water", in_water), ("deck", on_deck)]):
        e = RuleEngine(watches, [deck, water], Tuning(), TZ)
        fired = []
        for ts, img, box in water_frames(60):
            lying = TrackState(7, "person", box, t0, t0 + ts, zones_inside={"deck": t0})
            fired += [c.watch_id for c in e.on_tick("cam", Tick(t0 + ts, (640, 240), True, [lying], []), t0 + ts, img)]
        assert fired == ["deck"]


def test_the_flow_tool_belongs_to_each_engine():
    assert not hasattr(signals, "_dis")


# P5: an id swap at a line is not a crossing

def test_p5_id_swap_at_a_line_is_not_a_crossing():
    line = Zone("l1", "gate", [(0.5, 1.0), (0.5, 0.0)], "line")
    lt = LineTracker([line], Tuning().zones)

    class T:
        def __init__(self, i, x):
            self.id, self.cls, self.box, self.last_ts = i, "person", (x - 25, 250.0, 50.0, 150.0), 0.0

    ev = lt.update([T(1, 440), T(2, 560)], [], 0, (1000, 500))
    ev += lt.update([T(1, 560), T(2, 440)], [], 0.5, (1000, 500))
    assert ev == []
    ev = lt.update([T(1, 540)], [T(2, 440)], 1.0, (1000, 500)) + lt.update([T(1, 460)], [], 1.5, (1000, 500))
    assert [(e.track_id, e.direction) for e in ev] == [(1, "out")]  # a normal step still counts


# P6: zone_state goes stale

def gate_frame(state, night, seed, shift=0):
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
    st = ZoneStates(Tuning().zones)
    for night in (False, True):
        for s in ("open", "closed"):
            st.add(GATE, s, gate_frame(s, night, 1))
    return st


def zone_state_value(e):
    return e.shared["zone_state"]["gate"].get("state", "unknown")


def test_p6_an_unreadable_view_turns_the_state_unknown():
    w = W(Condition(signal="zone_state", zones=["gate"], op="is", value="open"), classes=(), cooldown_s=1)
    e = RuleEngine([("w", w)], [GATE], Tuning(), TZ, states=gate_states())
    t0 = at(23)
    e.on_tick("cam", Tick(t0, (320, 240), False, [], []), t0, gate_frame("open", False, 3))
    assert zone_state_value(e) == "open"
    blank = np.full((240, 320, 3), 128, np.uint8)
    for i in range(1, 600, 1):
        e.on_tick("cam", Tick(t0 + i, (320, 240), False, [], []), t0 + i, blank)
    assert zone_state_value(e) == "unknown"


def test_p6_the_state_is_unknown_while_the_camera_is_not_live():
    w = W(Condition(signal="zone_state", zones=["gate"], op="is", value="open"), classes=(), cooldown_s=1)
    e = RuleEngine([("w", w)], [GATE], Tuning(), TZ, states=gate_states())
    t0 = at(23)
    covered = EventRow(None, "cam", t0 + 1, "tamper_covered", None, None, None, {}, None, None, None, None)
    fired = [i for i in range(0, 20)
             if e.on_tick("cam", Tick(t0 + i, (320, 240), False, [], [covered] if i == 1 else []), t0 + i,
                          gate_frame("open", False, i))]
    assert fired == [0]  # covered from 1 s on: nothing is known about the gate, so it cannot become open again


def test_p6_a_time_window_starts_with_a_fresh_look():
    w = W(Condition(signal="zone_state", zones=["gate"], op="is", value="open"), classes=(), cooldown_s=1,
          time_window=TimeWindow(start="23:00", end="23:01"))
    e = RuleEngine([("w", w)], [GATE], Tuning(), TZ, states=gate_states())
    fired = []
    for day, state, start in ((23, "open", 0), (24, "closed", 2)):
        t = at(22, 59, start, day=day)
        while t < at(23, 2, day=day):
            if e.on_tick("cam", Tick(t, (320, 240), False, [], []), t, gate_frame(state, True, int(t))):
                fired.append(day)
            t += 1
    assert fired == [23]


def test_zone_state_is_worked_out_once_per_zone_for_all_watches():
    states = gate_states()
    calls = []
    classify = states.classify
    states.classify = lambda zone, image: calls.append(1) or classify(zone, image)
    a = W(Condition(signal="zone_state", zones=["gate"], op="is", value="open"), classes=())
    b = W(Condition(signal="zone_state", zones=["gate"], op="is", value="closed"), classes=())
    e = RuleEngine([("a", a), ("b", b)], [GATE], Tuning(), TZ, states=states)
    t0 = at(12)
    for i in range(30):
        e.on_tick("cam", Tick(t0 + i, (320, 240), False, [], []), t0 + i, gate_frame("open", False, i))
    assert len(calls) <= 30 / Tuning().zones.slow_tick_s + 1


# P7: a busy day backtests within the time limit

def test_p7_backtest_of_a_busy_day_stays_within_its_time_limit():
    cam = Camera("cam", "Lobby", "stream", None, [Zone("z", "lobby", [(0, 0), (1, 0), (1, 1), (0, 1)])])
    now = at(12) + 86400
    rows, tid, t = [], 0, now - 86400
    while t < now - 200:
        tid += 1
        rows += [row(t, "track_start", tid, size=[1000, 500]), row(t + 1, "enter", tid, "lobby"),
                 row(t + 170, "exit", tid, "lobby"), row(t + 171, "track_end", tid, zones=[])]
        t += 60
    w = W(Condition(signal="dwell_s", zones=["lobby"], op=">=", value=600), cooldown_s=60)
    start = time.perf_counter()
    r = backtest(w, ListRepo(cam, rows), ["cam"], now=now, tz=TZ)
    assert time.perf_counter() - start < Tuning().backtest.max_seconds + 3
    assert r.fires == 0 or r.capped
    night = W(Condition(signal="dwell_s", zones=["lobby"], op=">=", value=100), cooldown_s=60,
              time_window=TimeWindow(start="01:00", end="02:00"))
    r = backtest(night, ListRepo(cam, rows), ["cam"], now=now, tz=TZ)
    assert r.fires == 61 and not r.capped  # visits from 00:58 (already 119 s in at 01:00) to 01:58


# P8: lines and areas are not interchangeable

LINE = Zone("l1", "gate", [(0.5, 1.0), (0.5, 0.0)], "line")
LOBBY = Zone("a1", "lobby", [(0.6, 0.0), (1.0, 0.0), (1.0, 1.0), (0.6, 1.0)])
P8_CTX = CompileContext(zones={"lobby": "area", "gate": "line"}, purpose="main gate")
P8 = [("control", {"name": "In at gate", "subject": {"classes": ["person"]}, "verify": "?",
                   "conditions": [{"signal": "crossed", "params": {"line": "gate", "direction": "in"}}]}),
      ("watch zones is a line", {"name": "In at gate", "zones": ["gate"], "subject": {"classes": ["person"]},
                                 "conditions": [{"signal": "crossed", "params": {"line": "gate", "direction": "in"}}],
                                 "verify": "?"}),
      ("entered a line", {"name": "Enters gate", "subject": {"classes": ["person"]},
                          "conditions": [{"signal": "entered", "zones": ["gate"]}], "verify": "?"}),
      ("crossed an area", {"name": "Past lobby", "subject": {"classes": ["person"]},
                           "conditions": [{"signal": "crossed", "params": {"line": "lobby", "direction": "in"}}],
                           "verify": "?"}),
      ("crossed with an area in zones", {"name": "Past lobby", "subject": {"classes": ["person"]},
                                         "conditions": [{"signal": "crossed", "zones": ["lobby"]}], "verify": "?"})]


@pytest.mark.parametrize("label,watch", P8)
def test_p8_lines_only_in_crossed_and_areas_everywhere_else(label, watch):
    r = compile_watch("x", P8_CTX, FakeLLM([reply(watch)]))
    if label != "control":
        assert r.watch is None and r.cannot and ("line" in r.cannot[0].reason or "area" in r.cannot[0].reason)
        return
    rs = Reasoner(Camera("cam", "Gate", "stream", None, [LINE, LOBBY]), Tuning())
    e = RuleEngine([("w", r.watch)], [LINE, LOBBY], Tuning(), TZ)
    fires = 0
    for i, x in enumerate([100, 300, 450, 560, 700, 900]):
        ts = at(12) + i * 0.5
        seen = Observation(ts, 1, "person", (x - 25, 250.0, 50.0, 150.0), 0.9)
        tick = rs.step(Perception(ts, (1000, 500), True, [seen]))
        fires += len(e.on_tick("cam", tick, ts))
    assert fires == 1


def test_p8_the_ai_gets_a_second_chance_on_a_kind_mix_up():
    llm = FakeLLM([reply(P8[2][1]), reply(P8[0][1])])
    r = compile_watch("someone comes in at the gate", P8_CTX, llm)
    assert r.watch is not None and "line" in llm.calls[1][1][-1].text


def test_the_engine_refuses_a_kind_mix_up():
    with pytest.raises(ValueError):
        RuleEngine([("w", W(Condition(signal="in_zone", zones=["gate"])))], [LINE, LOBBY], Tuning(), TZ)


# minors

def test_alone_is_judged_with_both_heights_and_depth():
    t0 = at(21)
    w = W(Condition(signal="alone", zones=["water"]), cooldown_s=600)
    me = TrackState(1, "person", (140.0, 90.0, 40.0, 60.0), t0, t0, zones_inside={"water": t0})
    far_back = TrackState(2, "person", (244.0, 120.0, 12.0, 20.0), t0, t0)  # a few boxes off, far in depth
    beside = TrackState(3, "person", (190.0, 95.0, 38.0, 58.0), t0, t0)  # the same size, close by
    for others, alone in (([far_back], True), ([beside], False)):
        tick = Tick(t0, (320, 240), True, [me, *others], [])
        got = RuleEngine([("w", w)], [WATER], Tuning(), TZ).on_tick("cam", tick, t0)
        assert bool(got) == alone


def test_alert_cooldown_has_a_floor():
    t0 = at(12)
    w = W(Condition(signal="present"), cooldown_s=0, repeat=True)
    e = RuleEngine([("w", w)], [], Tuning(), TZ)
    p = TrackState(1, "person", (10.0, 10.0, 20.0, 50.0), t0, t0)
    fired = [i for i in range(60) if e.on_tick("cam", Tick(t0 + i, (320, 240), True, [p], []), t0 + i)]
    assert len(fired) <= 60 / Tuning().signals.alert_cooldown_floor_s + 1


def test_boy_and_girl():
    assert mentions_age("a boy alone at the pool") and mentions_age("girls in the gym")
    assert not mentions_age("log every delivery boy") and not mentions_age("the office boy arrives")


def test_select_watches_on_the_real_repository(tmp_path):
    from watchful.store.db import SqliteRepository, WatchRow
    repo = SqliteRepository(tmp_path / "w.db")
    pool = repo.add_camera(Camera(None, "Pool", "stream", None, [WATER]))
    gate = repo.add_camera(Camera(None, "Gate", "stream", None, [LINE]))
    site = W(Condition(signal="camera_health", op="!=", value="live"), classes=(), scope="site").model_dump()
    water = W(Condition(signal="in_zone", zones=["water"]), scope="site").model_dump()
    repo.add_watch(WatchRow(None, pool.id, "camera covered", site))
    repo.add_watch(WatchRow(None, pool.id, "in the water", water))
    later = repo.add_camera(Camera(None, "Lobby", "stream", None, [LOBBY]))
    for cam, want in ((pool, ["camera covered", "in the water"]), (gate, ["camera covered"]),
                      (later, ["camera covered"])):
        ok, skipped = select_watches(repo.list_watches(cam.id), cam.id, cam.zones)
        assert [w.name for _, w in ok] == ["w"] * len(want) and len(ok) == len(want)
    _, skipped = select_watches(repo.list_watches(gate.id), gate.id, gate.zones)
    assert skipped and "water" in skipped[0]


def test_pool_hours_come_from_data():
    llm = FakeLLM([reply(None)])
    compile_watch("someone in the water after pool hours", CompileContext(zones={"water": "water"}), llm)
    from watchful.agent.compiler import _words
    hours = _words()["default_hours"]
    assert "pool" in hours and json.dumps(hours) in llm.calls[0][0]
    assert "20:00 to 06:00 unless" not in llm.calls[0][0]


def test_watch_routes_check_everything_again_and_restart_where_needed(tmp_path):
    from accounts import SOCIETY, browser
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings

    manager = StubManager()
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=manager)
    c = browser(app)
    pool = c.post("/api/cameras", json={"name": "Pool", "kind": "stream", "url": "rtsp://x"}).json()["id"]
    gate = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://y"}).json()["id"]
    c.patch(f"/api/cameras/{pool}", json={"zones": [
        {"id": "w1", "name": "water", "kind": "water", "polygon": [[0, 0.2], [1, 0.2], [1, 1], [0, 1]]}]})
    c.patch(f"/api/cameras/{gate}", json={"zones": [
        {"id": "l1", "name": "gate", "kind": "line", "polygon": [[0.5, 1], [0.5, 0]]},
        {"id": "k1", "name": "kids play area", "polygon": [[0, 0], [0.4, 0], [0.4, 1], [0, 1]]}]})

    def save(camera, watch, **extra):
        return c.post(f"/api/cameras/{camera}/watches", json={"watch": watch, **extra})

    site = W(Condition(signal="camera_health", op="!=", value="live"), classes=(), scope="site").model_dump()
    row = save(pool, site).json()
    manager.started.clear()
    assert c.patch(f"/api/watches/{row['id']}", json={"enabled": False}).status_code == 200
    assert set(manager.started) == {pool, gate}  # a site watch switched off stops on every camera

    on_line = W(Condition(signal="in_zone", zones=["gate"])).model_dump()
    r = save(gate, on_line)
    assert r.status_code == 400 and "line" in r.json()["detail"]
    other = W(Condition(signal="present")).model_dump()
    other["subject"]["same_as"] = {"crop_key": "ws_someone_else/cam_x/kf/1.jpg"}
    assert save(gate, other).status_code == 400

    kids = W(Condition(signal="in_zone", zones=["kids play area"])).model_dump()
    kids["name"] = "Someone in the kids play area at night"
    r = save(gate, kids, coverage=[{"clause": "in the kids play area", "signal": "in_zone", "tier": "A"}])
    assert r.status_code == 200 and r.json()["watch"]["name"] == "Someone in the kids play area at night"
    ledger = W(Condition(signal="crossed", params={"line": "gate"}), classes=("car",), output="log").model_dump()
    r = save(gate, ledger)
    assert r.status_code == 200 and r.json()["watch"]["output"] == "log"  # the card said log, so it stays a log
    still = W(Condition(signal="still_s", zones=["water"], op=">=", value=20)).model_dump()
    r = save(pool, still, coverage=[{"clause": "still", "signal": "still_s", "tier": "A", "caveat": ""}])
    assert r.json()["coverage"][0]["tier"] == "B"  # a client can't claim more than the block gives

    child = json.dumps({"watch": {"name": "Child alone at the pool", "subject": {"classes": ["person"]},
                                  "conditions": [{"signal": "alone", "zones": ["water"]},
                                                 {"signal": "looks_like", "params": {"text": "a child"}}],
                                  "verify": "?"}, "coverage": [], "setup": [], "cannot": []})
    app.state.ctx.llm = FakeLLM([child])
    before = len(c.get(f"/api/cameras/{pool}/watches").json())
    r = c.post(f"/api/cameras/{pool}/watches", json={"text": "tell me when a child is alone at the pool"}).json()
    assert r["saved"] is False and r["cannot"] and r["watch"]["name"] == "Someone alone at the pool"
    assert len(c.get(f"/api/cameras/{pool}/watches").json()) == before  # nothing saved without asking

    jpg = cv2.imencode(".jpg", np.full((60, 80, 3), (20, 120, 200), np.uint8))[1].tobytes()
    ex = c.post(f"/api/cameras/{pool}/zones/w1/examples", data={"state": "covered"},
                files={"file": ("p.jpg", jpg, "image/jpeg")}).json()
    key = app.state.ctx.repo_for(SOCIETY).list_state_examples(pool)[0][3]
    assert key.startswith(f"{SOCIETY}/{pool}/states/")  # pictures live under their society
    picture = tmp_path / "blobs" / key
    assert picture.exists()
    manager.started.clear()
    assert c.delete(f"/api/examples/{ex['id']}").status_code == 200
    assert manager.started == [pool] and not picture.exists()
    c.post(f"/api/cameras/{pool}/zones/w1/examples", data={"state": "covered"},
           files={"file": ("p.jpg", jpg, "image/jpeg")})
    assert len(list((tmp_path / "blobs").rglob("*_day.jpg"))) == 1
    assert c.delete(f"/api/cameras/{pool}").status_code == 200
    assert list((tmp_path / "blobs").rglob("*_day.jpg")) == []


# fix round 2

def test_a_bare_age_word_as_a_zone_name_never_hides_an_age():
    assert mentions_age("children near the water", ["child"]) and mentions_age("kids near the gate", ["Kids"])
    assert not mentions_age("someone in the kids play area", ["kids play area"])
    assert not mentions_age("someone in the children's park", ["children's park"])
    assert mentions_age("a child in the children's park", ["children's park"])
    assert bare_age_name("Kids") and bare_age_name("child") and bare_age_name("the kids")
    assert not bare_age_name("kids play area") and not bare_age_name("gate")
    spec = W(Condition(signal="looks_like", params={"text": "kids"}), Condition(signal="in_zone", zones=["Kids"]),
             classes=("person",)).model_dump()
    spec["name"] = "Kids alone at the pool"
    with pytest.raises(Invalid):
        check_watch(spec, "kids alone at the pool", CompileContext(zones={"Kids": "area"}))
    spec["name"] = "Someone at the pool"
    with pytest.raises(Invalid):  # what a picture is compared with is never a place
        check_watch(spec, "someone at the pool", CompileContext(zones={"Kids": "area"}))
    play = W(Condition(signal="looks_like", params={"text": "someone in the kids play area"}),
             classes=("person",)).model_dump()
    with pytest.raises(Invalid):
        check_watch(play, "x", CompileContext(zones={"kids play area": "area"}))


def test_a_child_zone_name_does_not_let_a_child_watch_through():
    ctx = CompileContext(zones={"child": "area", "water": "water"})
    ai = reply({"name": "A child near the water", "subject": {"classes": ["person"]}, "verify": "Is a child there?",
                "conditions": [{"signal": "in_zone", "zones": ["water"]},
                               {"signal": "looks_like", "params": {"text": "a child", "not_text": "an adult"}}]})
    r = compile_watch("a child near the water", ctx, FakeLLM([ai]))
    assert r.cannot and any(mentions_age(c.clause) for c in r.cannot)
    assert r.watch is not None and [c.signal for c in r.watch.conditions] == ["in_zone"]
    assert not mentions_age(r.watch.name) and not mentions_age(r.watch.verify)


class _Box:
    def __init__(self, i, x, w=50.0, h=150.0):
        self.id, self.cls, self.box, self.last_ts = i, "person", (x - w / 2, 400.0 - h, w, h), 0.0


def test_a_real_crossing_beside_someone_standing_at_the_line_counts():
    lt = LineTracker([LINE], Tuning().zones)
    ev = lt.update([_Box(1, 450, 120, 80), _Box(2, 540)], [], 0, (1000, 500))
    ev += lt.update([_Box(1, 550, 120, 80), _Box(2, 540)], [], 0.5, (1000, 500))
    assert [(e.track_id, e.direction) for e in ev] == [(1, "in")]
    alone = LineTracker([LINE], Tuning().zones)
    ev = alone.update([_Box(1, 450, 120, 80)], [], 0, (1000, 500))
    assert [(e.track_id, e.direction) for e in ev + alone.update([_Box(1, 550, 120, 80)], [], 0.5, (1000, 500))] == \
        [(1, "in")]


def test_jumping_onto_the_spot_of_someone_who_just_went_is_a_swap():
    lt = LineTracker([LINE], Tuning().zones)
    ev = lt.update([_Box(1, 440), _Box(2, 560)], [], 0, (1000, 500))
    ev += lt.update([_Box(1, 560)], [_Box(2, 560)], 0.5, (1000, 500))
    assert ev == []


def test_a_swimmer_beside_a_standing_lifeguard_is_not_alone():
    t0 = at(15)
    w = W(Condition(signal="alone", zones=["water"]), cooldown_s=600)
    swimmer = TrackState(1, "person", (150.0, 120.0, 30.0, 30.0), t0, t0, zones_inside={"water": t0})
    lifeguard = TrackState(2, "person", (190.0, 0.0, 50.0, 150.0), t0, t0)
    tick = Tick(t0, (320, 240), True, [swimmer, lifeguard], [])
    assert RuleEngine([("w", w)], [WATER], Tuning(), TZ).on_tick("cam", tick, t0) == []
    tick = Tick(t0 + 1, (320, 240), True, [swimmer], [])
    assert len(RuleEngine([("w", w)], [WATER], Tuning(), TZ).on_tick("cam", tick, t0 + 1)) == 1


def test_a_watch_that_no_longer_parses_can_still_be_switched_off(tmp_path):
    from accounts import SOCIETY, browser
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings
    from watchful.store.db import WatchRow

    manager = StubManager()
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                     manager=manager)
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://y"}).json()["id"]
    repo = app.state.ctx.repo_for(SOCIETY)
    row = repo.add_watch(WatchRow(None, cam, "an old watch", {"name": "old", "conditions": [{"signal": "gone"}]}))
    manager.started.clear()
    r = c.patch(f"/api/watches/{row.id}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and manager.started == [cam]
    assert [w.enabled for w in repo.list_watches(cam)] == [False]
    bad = c.patch(f"/api/cameras/{cam}", json={"zones": [{"id": "z", "name": "Kids", "polygon": [[0, 0], [1, 0],
                                                                                                    [1, 1]]}]})
    assert bad.status_code == 400 and "place" in bad.json()["detail"]
