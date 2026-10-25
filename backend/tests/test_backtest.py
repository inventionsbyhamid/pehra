from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from watchful.rules.backtest import backtest
from watchful.rules.watch import Condition, Subject, TimeWindow, Watch
from watchful.store.db import Camera, EventRow, SqliteRepository
from watchful.vision.zones import Zone

TZ = "Asia/Kolkata"
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=ZoneInfo(TZ))
WATER = Zone("w1", "water", [(0.1, 0.2), (0.9, 0.2), (0.9, 0.9), (0.1, 0.9)], "water")
DECK = Zone("d1", "deck", [(0.0, 0.0), (1.0, 0.0), (1.0, 0.2), (0.0, 0.2)])
GATE = Zone("l1", "gate", [(0.5, 1.0), (0.5, 0.0)], "line")
BOX = [100.0, 100.0, 40.0, 80.0]


def ev(camera, ts, kind, tid, zone=None, cls="person", **data):
    return EventRow(id=None, camera_id=camera, ts=ts, kind=kind, track_id=tid, cls=cls, zone=zone,
                    data={"box": BOX, **data}, keyframe_key=None, signature=None, embedding=None, person_label=None)


def visit(camera, start: datetime, tid: int, zone: str, minutes=2, cls="person"):
    t = start.timestamp()
    return [ev(camera, t, "track_start", tid, cls=cls, size=[1000, 500]), ev(camera, t + 5, "enter", tid, zone, cls),
            ev(camera, t + minutes * 60, "exit", tid, zone, cls), ev(camera, t + minutes * 60 + 2, "track_end", tid,
                                                                    cls=cls, zones=[], duration_s=minutes * 60)]


class ListRepo:
    """Only what backtest may use: the Repository protocol's reads."""

    def __init__(self):
        self.cameras, self.rows = {}, []

    def add_camera(self, cam):
        self.cameras[cam.id] = cam
        return cam

    def get_camera(self, camera_id):
        return self.cameras.get(camera_id)

    def add_events(self, rows):
        self.rows += rows

    def list_enrollments(self):
        return []

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None, limit=500):
        out = [r for r in self.rows if (camera_id is None or r.camera_id == camera_id)
               and (not kinds or r.kind in kinds) and (since is None or r.ts >= since)
               and (until is None or r.ts <= until)]
        return sorted(out, key=lambda r: r.ts)[-limit:]


@pytest.fixture(params=["list", "sqlite"])
def repo(request, tmp_path):
    r = ListRepo() if request.param == "list" else SqliteRepository(tmp_path / "w.db")
    r.add_camera(Camera("cam_pool", "Pool", "stream", None, [WATER, DECK]))
    r.add_camera(Camera("cam_gate", "Main gate", "stream", None, [GATE]))
    return r


def day(hh, mm=0, days_ago=1):
    return (NOW - timedelta(days=days_ago)).replace(hour=hh, minute=mm)


def test_backtest_counts_yesterdays_fires(repo):
    rows = (visit("cam_pool", day(22, 10), 1, "water") + visit("cam_pool", day(23, 30), 2, "water")
            + visit("cam_pool", NOW.replace(hour=1, minute=15), 3, "water")
            + visit("cam_pool", day(15, 0), 4, "water") + visit("cam_pool", day(22, 40), 5, "deck")
            + visit("cam_pool", day(22, 30, days_ago=2), 6, "water"))
    repo.add_events(rows)
    w = Watch(name="In the water after pool hours", subject=Subject(classes=["person"]),
              conditions=[Condition(signal="in_zone", zones=["water"])],
              time_window=TimeWindow(start="22:00", end="06:00"), severity="critical", verify="?")
    r = backtest(w, repo, ["cam_pool"], now=NOW.timestamp(), tz=TZ)
    assert r.fires == 3 and len(r.examples) == 3
    assert all("Pool" in e for e in r.examples) and "10:10 PM" in r.examples[0]
    assert not r.assumed and r.summary == "Would have fired 3 times."


def test_backtest_replays_crossings_by_direction(repo):
    t = day(18).timestamp()
    rows = []
    for i, direction in enumerate(["in", "in", "out"]):
        rows += [ev("cam_gate", t + i * 600, "track_start", 10 + i, cls="car"),
                 ev("cam_gate", t + i * 600 + 3, "cross", 10 + i, "gate", "car", direction=direction),
                 ev("cam_gate", t + i * 600 + 9, "track_end", 10 + i, cls="car", zones=[])]
    repo.add_events(rows)
    w = Watch(name="Cars in", subject=Subject(classes=["car"]), output="log", verify="",
              conditions=[Condition(signal="crossed", params={"line": "gate", "direction": "in"})])
    assert backtest(w, repo, ["cam_gate"], now=NOW.timestamp(), tz=TZ).fires == 2


def test_backtest_says_what_it_cannot_replay(repo):
    repo.add_events(visit("cam_pool", day(22, 10), 1, "water", minutes=5))
    w = Watch(name="Still in the water", subject=Subject(classes=["person"]), verify="?",
              conditions=[Condition(signal="in_zone", zones=["water"]),
                          Condition(signal="still_s", zones=["water"], op=">=", value=20)])
    r = backtest(w, repo, ["cam_pool", "cam_gate"], now=NOW.timestamp(), tz=TZ)
    assert r.assumed == ["still_s"] and r.fires == 1 and r.visits == 1
    assert r.summary == "About 1 visit, assuming staying still held."
    assert any("Main gate" in s for s in r.skipped)  # no water zone there
