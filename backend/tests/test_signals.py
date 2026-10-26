from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from watchful.rules.engine import RuleEngine
from watchful.rules.presets import preset
from watchful.rules.watch import Condition, SameAs, Subject, Watch
from watchful.tuning import Tuning
from watchful.vision.pose import PoseResult
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone

TZ = "Asia/Kolkata"
ZONES = [Zone("z1", "back door", [(0, 0), (0.3, 0), (0.3, 1), (0, 1)]),
         Zone("z2", "kitchen window", [(0.3, 0), (0.6, 0), (0.6, 1), (0.3, 1)]),
         Zone("z3", "gate", [(0.6, 0), (1, 0), (1, 1), (0.6, 1)])]
SIZE = (1000, 500)


def at(hh, mm=0):
    return datetime(2026, 10, 23, hh, mm, tzinfo=ZoneInfo(TZ)).timestamp()


def person(tid, zones, first, known=None, pose=None, embedding=None, signature=None, box=(100.0, 100.0, 50.0, 150.0)):
    return TrackState(tid, "person", box, first, first, zones_inside=zones, known_person_id=known, pose=pose,
                      fresh_pose=pose is not None, embedding=embedding, signature=signature)


def tick(ts, tracks):
    return Tick(ts, SIZE, True, tracks, [])


def run(watch, frames, embedder=None, image=None):
    e = RuleEngine([("w", watch)], ZONES, Tuning(), TZ, embedder=embedder)
    out = []
    for ts, tracks in frames:
        out += e.on_tick("cam", tick(ts, tracks), ts, image)
    return out


def intruder():
    return preset("possible_intruder", entries=["back door", "kitchen window"], climbs=["gate"])


def test_intruder_preset_lingering_unknown_at_night():
    t0 = at(23, 30)
    got = run(intruder(), [(t0 + i, [person(1, {"back door": t0}, t0)]) for i in range(0, 30, 1)])
    assert len(got) == 1 and any("back door" in r for r in got[0].evidence["reasons"])
    assert got[0].severity == "critical"


def test_intruder_brief_visit_not_candidate():
    t0 = at(23, 30)
    assert run(intruder(), [(t0 + i, [person(1, {"back door": t0}, t0)]) for i in range(8)]) == []


def test_intruder_known_person_never_candidate():
    t0 = at(23, 30)
    assert run(intruder(), [(t0 + i, [person(1, {"back door": t0}, t0, known="p1")]) for i in range(60)]) == []


def pose_at(hip_y, wrist_y, nose_y=None, bh=150.0):
    lm = np.zeros((39, 5), np.float32)
    lm[:, 3] = 1.0
    lm[[23, 24], 1] = hip_y
    lm[[23, 24], 0] = 800
    lm[[15, 16], 1] = wrist_y
    lm[[15, 16], 0] = 800
    lm[0, 1] = nose_y if nose_y is not None else hip_y - 100
    lm[0, 0] = 800
    return PoseResult(lm, 10.0, hip_y, bh, 0.9)


def test_climbing_signal():
    w = Watch(name="climb", verify="?", conditions=[Condition(signal="climbing", zones=["gate"])])
    t0 = at(2)
    frames = [(t0 + i * 0.5, [person(1, {"gate": t0}, t0, pose=pose_at(400 - 45 * i, 200 - 45 * i, 300 - 45 * i),
                                     box=(780.0, 250.0 - 45 * i, 50.0, 150.0))]) for i in range(5)]
    got = run(w, frames)
    assert len(got) == 1 and any("climb" in r for r in got[0].evidence["reasons"])


class StubEmbedder:
    def __init__(self, texts):
        self.texts = texts

    def text(self, q):
        return np.array(self.texts.get(q, [0.6, 0.6]), np.float32)

    def image(self, crop):
        v = np.array([crop.mean() / 255.0, 1 - crop.mean() / 255.0], np.float32)
        return v / np.linalg.norm(v)


def test_lookout_looks_like_matches_only_the_right_track():
    w = Watch(name="red jacket", subject=Subject(looks_like="a man in a red jacket"), verify="?",
              conditions=[Condition(signal="present")])
    emb = StubEmbedder({"a man in a red jacket": [1.0, 0.0]})
    t0 = at(12)
    got = run(w, [(t0, [person(1, {}, t0, embedding=np.array([1.0, 0.0], np.float32)),
                        person(2, {}, t0, embedding=np.array([0.0, 1.0], np.float32))])], embedder=emb)
    assert [c.track_id for c in got] == [1] and got[0].evidence["method"] == "description"


def test_same_as_expires():
    t0 = at(12)
    target = SameAs(embedding=[1.0, 0.0], expires_at=t0 + 100)
    w = Watch(name="again", subject=Subject(same_as=target), verify="?", conditions=[Condition(signal="present")])
    vec = np.array([1.0, 0.0], np.float32)
    assert len(run(w, [(t0, [person(1, {}, t0, embedding=vec)])])) == 1
    assert run(w, [(t0 + 200, [person(2, {}, t0 + 200, embedding=vec)])]) == []


def test_zone_looks_like_needs_hold():
    w = preset("gate_left_open", zone="gate", hold=10)
    emb = StubEmbedder({"an open gate": [1.0, 0.0], "a closed gate": [0.0, 1.0]})
    bright = np.full((500, 1000, 3), 250, np.uint8)
    t0 = at(12)
    got = []
    e = RuleEngine([("w", w)], ZONES, Tuning(), TZ, embedder=emb)
    for i in range(0, 16, 3):
        got.append((i, e.on_tick("cam", tick(t0 + i, []), t0 + i, bright)))
    fired = [i for i, c in got if c]
    assert fired and fired[0] >= 10


def test_looks_like_contrast_for_people_without_glasses():
    w = preset("looks_like_check", text="a person not wearing glasses", not_text="a person wearing glasses")
    # the texts the model compares carry no "not": the plain subject against the glasses (test_negated_looks.py)
    emb = StubEmbedder({"a person": [1.0, 0.0], "a person wearing glasses": [0.0, 1.0]})
    t0 = at(12)
    without = person(1, {}, t0, embedding=np.array([0.9, 0.1], np.float32) / np.linalg.norm([0.9, 0.1]))
    with_glasses = person(2, {}, t0, embedding=np.array([0.1, 0.9], np.float32) / np.linalg.norm([0.1, 0.9]))
    got = run(w, [(t0, [without, with_glasses])], embedder=emb)
    assert [c.track_id for c in got] == [1]
    # what OpenCV saw stays neutral: told "not a person wearing glasses", the AI check raised a man in glasses 6 of 6
    assert got[0].evidence["reasons"] == ["looks like a person for 0 s"]


def test_compiler_prompt_offers_appearance_checks():
    from watchful.agent.prompting import render
    from watchful.rules.signals import catalog
    text = render("compile", catalog=catalog(), classes=[], vocabulary=[], zones=[], presets=[], people={})
    assert "looks_like" in text and "glasses" in text and "helmet" in text
