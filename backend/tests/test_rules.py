from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from watchful.rules.engine import RuleEngine, needs_for
from watchful.rules.presets import preset, preset_names
from watchful.rules.watch import Condition, Subject, TimeWindow, Watch
from watchful.store.db import EventRow
from watchful.tuning import Tuning
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone

TZ = "Asia/Kolkata"
ZONES = [Zone("z1", "parking lot", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)]),
         Zone("z2", "back door", [(0.5, 0), (1, 0), (1, 1), (0.5, 1)])]


def at(hh, mm=0):
    return datetime(2026, 10, 23, hh, mm, tzinfo=ZoneInfo(TZ)).timestamp()


def track(tid, cls="person", zones=None, posture="upright", first=0.0, known=None):
    return TrackState(tid, cls, (10.0, 10.0, 40.0, 90.0), first, first, zones_inside=zones or {}, posture=posture,
                      known_person_id=known)


def tick(ts, tracks, events=(), pos=None):
    return Tick(ts, (640, 480), True, list(tracks), list(events), pos=pos)


def enter(tid, zone, ts, cls="person"):
    return EventRow(None, "cam", ts, "enter", tid, cls, zone, {}, None, None, None, None)


def engine(*watches, kind="stream"):
    return RuleEngine([(f"w{i}", w) for i, w in enumerate(watches)], ZONES, Tuning(), TZ, kind)


def test_enter_preset_matches_subject_and_zone():
    w = preset("enters", zone="parking lot", classes=["car", "truck"])
    e = engine(w)
    t0 = at(12)
    assert e.on_tick("cam", tick(t0, [track(1, "person", {"parking lot": t0})], [enter(1, "parking lot", t0)]), t0) == []
    got = e.on_tick("cam", tick(t0, [track(2, "car", {"parking lot": t0})], [enter(2, "parking lot", t0, "car")]), t0)
    assert len(got) == 1 and got[0].track_id == 2 and got[0].watch_id == "w0"


def test_dwell_fires_once_after_value():
    e = engine(preset("stays_too_long", zone="back door", value=20))
    t0, fired = at(12), []
    for i in range(0, 60, 2):
        fired += e.on_tick("cam", tick(t0 + i, [track(1, zones={"back door": t0})]), t0 + i)
    assert len(fired) == 1 and fired[0].ts == t0 + 20
    assert any("back door" in r for r in fired[0].evidence["reasons"])


def test_time_window_wraps_midnight():
    tw = TimeWindow(start="22:00", end="06:00")
    assert tw.contains(at(23, 30), TZ) and tw.contains(at(5), TZ) and not tw.contains(at(12), TZ)


def test_fallen_posture():
    e = engine(preset("fall"))
    t0 = at(12)
    assert e.on_tick("cam", tick(t0, [track(1, posture="falling")]), t0) == []
    assert len(e.on_tick("cam", tick(t0 + 1, [track(1, posture="fallen")]), t0 + 1)) == 1


def test_count_scene_signal():
    e = engine(preset("too_many", zone="parking lot", value=3, classes=["person"]))
    t0 = at(12)
    two = [track(i, zones={"parking lot": t0}) for i in (1, 2)]
    assert e.on_tick("cam", tick(t0, two), t0) == []
    got = e.on_tick("cam", tick(t0 + 1, two + [track(3, zones={"parking lot": t0})]), t0 + 1)
    assert len(got) == 1 and got[0].track_id is None


def score_watch(combine="score"):
    return Watch(name="s", subject=Subject(), combine=combine, threshold=2, verify="?", conditions=[
        Condition(signal="dwell_s", zones=["back door"], value=20, weight=2),
        Condition(signal="in_zone", zones=["parking lot"], weight=1)])


def test_score_combine():
    t0 = at(12)
    assert len(engine(score_watch()).on_tick("cam", tick(t0 + 25, [track(1, zones={"back door": t0})]), t0 + 25)) == 1
    assert engine(score_watch()).on_tick("cam", tick(t0, [track(2, zones={"parking lot": t0})]), t0) == []


def test_any_combine():
    t0 = at(12)
    assert len(engine(score_watch("any")).on_tick("cam", tick(t0, [track(2, zones={"parking lot": t0})]), t0)) == 1


def test_scene_watch_fires_once_while_true_and_again_after_clearing():
    e = engine(preset("too_many", zone="parking lot", value=1, classes=["person"]))
    t0, fired = at(12), []
    for i in range(0, 300, 10):
        busy = i < 60 or i >= 200
        fired += e.on_tick("cam", tick(t0 + i, [track(1, zones={"parking lot": t0})] if busy else []), t0 + i)
    assert [c.ts - t0 for c in fired] == [0, 200]


def test_a_second_fall_on_the_same_track_alerts_again():
    e = engine(preset("fall"))
    t0, fired = at(12), []
    for i, posture in enumerate(["fallen", "fallen", "upright", "upright", "fallen"]):
        fired += e.on_tick("cam", tick(t0 + 30 * i, [track(1, posture=posture)]), t0 + 30 * i)
    assert len(fired) == 2


def test_demo_loop_same_fingerprint():
    w = preset("enters", zone="parking lot", classes=["person"])
    a = engine(w, kind="demo").on_tick("cam", tick(at(12), [track(1, zones={"parking lot": 0})],
                                                   [enter(1, "parking lot", at(12))], pos=31.0), at(12))
    b = engine(w, kind="demo").on_tick("cam", tick(at(13), [track(9, zones={"parking lot": 0})],
                                                   [enter(9, "parking lot", at(13))], pos=31.4), at(13))
    assert a[0].fingerprint == b[0].fingerprint


def test_unknown_signal_rejected():
    w = Watch(name="x", verify="?", conditions=[Condition(signal="teleported")])
    with pytest.raises(ValueError, match="teleported"):
        engine(w)


def test_every_preset_is_valid():
    for name in preset_names():
        engine(preset(name, zone="parking lot", value=3, classes=["person"]))


def test_needs_follow_watches():
    n = needs_for([preset("fall"), preset("enters", zone="parking lot", classes=["car"])])
    assert n.posture and not n.pose and not n.faces and n.classes == {"person", "car"}


def exit_event(tid, zone, ts):
    return EventRow(None, "cam", ts, "exit", tid, "person", zone, {}, None, None, None, None)


def test_watch_zone_counts_a_track_that_just_left_it():
    w = Watch(name="leaves", zones=["back door"], verify="?",
              conditions=[Condition(signal="exited", zones=["back door"])])
    t0 = at(12)
    got = engine(w).on_tick("cam", tick(t0, [track(1, zones={})], [exit_event(1, "back door", t0)]), t0)
    assert len(got) == 1
