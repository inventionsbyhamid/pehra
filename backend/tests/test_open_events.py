"""Open ended events with no AI cost per frame: a local image and text model watches, the AI only confirms."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from watchful.rules.engine import RuleEngine
from watchful.rules.watch import Condition, Watch
from watchful.tuning import Tuning
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone

TZ = "Asia/Kolkata"
ZONES = [Zone("z1", "kitchen", [(0, 0), (1, 0), (1, 1), (0, 1)])]
EATING = Watch(name="Someone eating", verify="Is this person eating?", conditions=[
    Condition(signal="looks_like", params={"text": "a person eating", "not_text": "a person not eating",
                                           "hold_s": 4})])


class Stub:
    def text(self, q):
        return np.array([1.0, 0.0] if q == "a person eating" else [0.0, 1.0], np.float32)


def person(emb):
    v = np.asarray(emb, np.float32)
    return TrackState(1, "person", (10.0, 10.0, 40.0, 90.0), 0.0, 0.0, embedding=v / np.linalg.norm(v))


def at(hh):
    return datetime(2026, 10, 24, hh, tzinfo=ZoneInfo(TZ)).timestamp()


def test_an_action_must_hold_before_it_alerts():
    e = RuleEngine([("w", EATING)], ZONES, Tuning(), TZ, embedder=Stub())
    t0, fired = at(13), []
    looks = [0.9, 0.2, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]  # one stray frame, then eating for real
    for i, x in enumerate(looks):
        fired += e.on_tick("cam", Tick(t0 + i, (640, 480), True, [person([x, 1 - x])], []), t0 + i)
    assert [c.ts - t0 for c in fired] == [6]  # held from 2 s to 6 s
    assert "verify" not in fired[0].evidence.get("method", "")  # the agent still confirms it, once


def test_compiler_turns_actions_into_local_watches():
    from watchful.agent.prompting import render
    from watchful.rules.signals import catalog
    text = render("compile", catalog=catalog(), classes=[], vocabulary=[], zones=[], presets=[], people={})
    assert "eating" in text and "hold_s" in text and "never refuse" in text.lower()


def test_a_person_is_embedded_once_while_they_look_the_same(tmp_path):
    from test_pipeline import StubDetector
    from watchful.config import Settings
    from watchful.ingest.sources import Frame
    from watchful.store.blobs import LocalBlobStore
    from watchful.store.db import Camera
    from watchful.vision.perceive import Needs, Perceiver

    class Emb:
        calls = 0

        def image(self, crop):
            Emb.calls += 1
            return np.ones(2, np.float32) / np.sqrt(2)

    det = StubDetector()
    det.boxes = [(100.0, 100.0, 60.0, 150.0)]
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    p = Perceiver(Camera("cam", "c", "stream", None), s, Tuning(), LocalBlobStore(tmp_path), None,
                  Needs(classes={"person"}, embed=True), embedder=Emb(), detector=det)
    for i in range(50):  # 10 s at 5 fps with the person moving a little
        img = np.full((360, 640, 3), 40, np.uint8)
        img[100:250, 100 + i % 3:160 + i % 3] = 200
        p.perceive(Frame(img, i * 0.2, i))
    assert Emb.calls == 1  # 10 s in the same clothes: one look (Tuning embed.every_s and embed.change)
