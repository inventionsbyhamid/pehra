from watchful.vision.detect import Detection
from watchful.vision.track import Tracker

DT = 0.2


def det(x, y, w=40, h=80, score=0.9, cls="person"):
    return Detection(cls, score, (float(x), float(y), float(w), float(h)))


def test_id_stable_for_moving_box():
    tr = Tracker()
    ids = set()
    for i in range(20):
        up = tr.update([det(50 + 10 * i, 100)], ts=i * DT)
        ids |= {t.id for t in up.active}
    assert len(ids) == 1


def test_two_tracks_cross_keep_ids():
    tr = Tracker()
    first = {}
    for i in range(30):
        a = det(20 + 15 * i, 100)
        b = det(470 - 15 * i, 130)
        up = tr.update([a, b], ts=i * DT)
        for t in up.active:
            side = "a" if t.box[1] < 115 else "b"
            if i < 5:
                first.setdefault(side, t.id)
            elif i > 25:
                assert first[side] == t.id
    assert len(first) == 2


def test_track_ends_after_gap():
    tr = Tracker(max_age_s=2.0)
    for i in range(5):
        tr.update([det(100, 100)], ts=i * DT)
    ended = []
    for i in range(5, 5 + 12):
        ended += tr.update([], ts=i * DT).ended
    assert len(ended) == 1


def test_short_blip_never_starts():
    tr = Tracker(min_hits=2)
    started = tr.update([det(100, 100)], ts=0).started
    for i in range(1, 15):
        up = tr.update([], ts=i * DT)
        started += up.started
        assert up.ended == []
    assert started == []


def test_classes_do_not_mix():
    tr = Tracker()
    for i in range(5):
        up = tr.update([det(100, 100, cls="person"), det(102, 102, cls="dog")], ts=i * DT)
    assert sorted(t.cls for t in up.active) == ["dog", "person"]
