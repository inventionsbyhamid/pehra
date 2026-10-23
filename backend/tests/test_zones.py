from watchful.vision.track import Track
from watchful.vision.zones import Zone, ZoneTracker

SIZE = (640, 480)
LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])


def track(x, ts, tid=1):
    # foot point is (x + 20, 280)
    return Track(tid, "person", (float(x), 200.0, 40.0, 80.0), 0.0, ts)


def test_enter_then_exit_with_dwell():
    zt = ZoneTracker([LEFT])
    events = []
    for ts, x in [(5, 500), (10, 200), (15, 150), (25, 500)]:
        events += zt.update([track(x, ts)], [], ts, SIZE)
    kinds = [(e.kind, e.ts) for e in events]
    assert kinds == [("enter", 10), ("exit", 25)]
    assert events[1].dwell_s == 15
    assert events[0].zone == "left" and events[0].cls == "person"


def test_track_end_inside_emits_exit():
    zt = ZoneTracker([LEFT])
    zt.update([track(100, 1)], [], 1, SIZE)
    # the tracker reports the end 2 s after the last sighting; the exit is the last sighting
    events = zt.update([], [track(100, 2.5)], 4.5, SIZE)
    assert [(e.kind, e.ts, e.dwell_s) for e in events] == [("exit", 2.5, 1.5)]
    assert zt.inside(1) == {}


def test_point_on_edge_counts_inside():
    zt = ZoneTracker([LEFT])
    # foot x = 300 + 20 = 320, exactly the zone edge at 0.5 * 640
    events = zt.update([track(300, 1)], [], 1, SIZE)
    assert [e.kind for e in events] == ["enter"]
    assert zt.inside(1) == {"left": 1}
