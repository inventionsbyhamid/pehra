from watchful.vision.detect import Detection
from watchful.tuning import TrackTuning
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
    tr = Tracker(TrackTuning(max_age_s=2.0))
    for i in range(5):
        tr.update([det(100, 100)], ts=i * DT)
    ended = []
    for i in range(5, 5 + 12):
        ended += tr.update([], ts=i * DT).ended
    assert len(ended) == 1


def test_short_blip_never_starts():
    tr = Tracker(TrackTuning(min_hits=2))
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


def test_walkers_keep_their_ids_at_one_frame_a_second():
    """At 1 analysed frame a second a walker moves more than its own width: IoU alone restarts the id every frame."""
    tr = Tracker()
    ids = {"a": set(), "b": set()}
    for i in range(15):
        up = tr.update([det(50 + 55 * i, 100), det(900 - 50 * i, 300)], ts=float(i))
        for t in up.active:
            ids["a" if t.box[1] < 200 else "b"].add(t.id)
    assert [len(ids["a"]), len(ids["b"])] == [1, 1]


def test_a_car_keeps_its_id_at_one_frame_a_second():
    tr = Tracker()
    ids = set()
    for i in range(10):
        up = tr.update([det(40 + 150 * i, 200, w=120, h=60, cls="car")], ts=float(i))
        ids |= {t.id for t in up.active}
    assert len(ids) == 1  # 2.5 box heights a second: past a person's gate, inside a car's


def test_the_far_pass_never_joins_another_size_class_or_place():
    for far in (det(160, 100, w=120, h=240), det(160, 100, cls="dog"), det(100 + 80 * 4, 100)):
        tr = Tracker()
        for i in range(2):
            tr.update([det(100, 100)], ts=float(i))
        (person,) = tr.tracks
        tr.update([far], ts=2.0)  # three times the height, another class, four body heights in a second
        assert person.hits == 2, far


def test_clothes_break_a_tie_between_two_walkers():
    import cv2
    import numpy as np
    red, blue = (0, 0, 220), (220, 0, 0)

    def frame(boxes):
        img = np.zeros((400, 500, 3), np.uint8)
        for (x, y, w, h), colour in boxes:
            cv2.rectangle(img, (x, y), (x + w, y + h), colour, -1)
        return img

    tr = Tracker()
    for i in range(2):  # standing, so their predictions stay put
        a, b = (100, 100, 40, 80), (200, 100, 40, 80)
        tr.update([det(*a[:2]), det(*b[:2])], ts=float(i), image=frame([(a, red), (b, blue)]))
    by_x = {round(t.box[0]): t.id for t in tr.tracks}
    a, b = (170, 100, 40, 80), (130, 100, 40, 80)  # a second later they have walked past each other's nearest spot
    up = tr.update([det(*a[:2]), det(*b[:2])], ts=2.0, image=frame([(a, red), (b, blue)]))
    now = {round(t.box[0]): t.id for t in up.active}
    assert now[170] == by_x[100] and now[130] == by_x[200]  # the red one is still the red one
