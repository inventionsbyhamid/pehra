from watchful.vision.falls import FallDetector
from watchful.vision.pose import PoseResult

BH = 170.0


def pose(angle, hip_y, conf=0.9):
    return PoseResult(landmarks=None, torso_angle_deg=angle, hip_y=hip_y, body_height=BH, confidence=conf)


def upright_box():
    return (100.0, 200.0, 60.0, 170.0)


def lying_box():
    return (60.0, 340.0, 170.0, 60.0)


def run(samples, track_id=7):
    fd = FallDetector(track_id)
    events = []
    for ts, p, box in samples:
        e = fd.update(ts, p, box)
        if e:
            events.append(e)
    return events


def test_quick_drop_then_lying_gives_falling_then_fallen():
    s = [(t * 0.5, pose(5, 300), upright_box()) for t in range(5)]
    s += [(2.5, pose(45, 360), upright_box()), (3.0, pose(80, 400), lying_box())]
    s += [(3.0 + t * 0.5, pose(85, 400), lying_box()) for t in range(1, 7)]
    events = run(s)
    assert [e.kind for e in events] == ["falling", "fallen"]
    assert events[0].ts == 2.5 and events[1].ts >= 5.0
    assert events[0].track_id == 7


def test_slowly_lying_down_is_not_falling():
    s = []
    for i in range(17):
        t = i * 0.5
        angle = min(85.0, 5 + 80 * t / 6)
        k = min(t, 6) / 6  # the box turns from tall to wide as slowly as the body
        box = tuple(a + (b - a) * k for a, b in zip(upright_box(), lying_box()))
        s.append((t, pose(angle, 300 + 100 * k), box))
    events = run(s)
    assert [e.kind for e in events] == ["fallen"]
    first_lying = next(t for t, p, b in s if p.torso_angle_deg > 60 or b[2] / b[3] > 1.3)
    assert events[0].ts >= first_lying + 2


def test_sitting_down_no_events():
    s = [(t * 0.5, pose(5, 300), upright_box()) for t in range(4)]
    s += [(2.0 + t * 0.5, pose(20, 351), (100.0, 250.0, 80.0, 120.0)) for t in range(8)]
    assert run(s) == []


def test_recovered_after_getting_up():
    s = [(t * 0.5, pose(85, 400), lying_box()) for t in range(6)]
    s += [(3.0 + t * 0.5, pose(10, 300), upright_box()) for t in range(6)]
    assert [e.kind for e in run(s)] == ["fallen", "recovered"]


def test_missing_pose_uses_box_shape():
    s = [(t * 0.5, None, upright_box()) for t in range(3)]
    s += [(1.5 + t * 0.5, None, lying_box()) for t in range(6)]
    events = run(s)
    fallen = [e for e in events if e.kind == "fallen"]
    assert len(fallen) == 1 and fallen[0].confidence < 0.7


def test_box_only_fall_gives_falling():
    s = [(t * 0.5, None, upright_box()) for t in range(5)]
    s += [(2.5, None, (70.0, 300.0, 120.0, 90.0))]
    s += [(3.0 + t * 0.5, None, lying_box()) for t in range(6)]
    events = run(s)
    assert [e.kind for e in events] == ["falling", "fallen"]
    assert events[0].ts == 2.5 and events[0].confidence < 0.7
