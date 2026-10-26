"""Text watches embed far less: only tracks and classes a watch can use, once on a first stable look, again only on
a change of clothes or after Tuning embed.every_s, and at most embed.max_per_frame a frame, new tracks first."""
import cv2
import numpy as np

from watchful.config import Settings
from watchful.ingest.sources import Frame
from watchful.rules.engine import RuleEngine, needs_for
from watchful.rules.presets import preset
from watchful.rules.watch import Condition, Subject, Watch
from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.detect import Detection
from watchful.vision.motion import MotionResult
from watchful.vision.perceive import Needs, Perceiver
from watchful.vision.zones import Zone

LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])
SIZE = (1000, 400)


class Boxes:
    """A detector that sees the boxes it is given."""
    name = "fixed"

    def __init__(self, boxes):
        self.boxes = boxes

    def set_classes(self, classes):
        pass

    def detect(self, image):
        return [Detection(cls, 0.9, box) for cls, box in self.boxes]


class Counting:
    def __init__(self):
        self.crops = []

    def image(self, crop):
        self.crops.append(crop)
        return np.full(4, 0.5, np.float32)


class NoBlobs:
    def put(self, *a):
        pass


def picture(boxes, colours):
    img = np.full((SIZE[1], SIZE[0], 3), 90, np.uint8)
    for (_, (x, y, w, h)), colour in zip(boxes, colours):
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), colour, -1)
    return img


def perceiver(tmp_path, boxes, needs, zones=()):
    s = Settings(session_secret="y", data_dir=tmp_path, _env_file=None)
    p = Perceiver(Camera("cam_b", "Plaza", "stream", None, list(zones)), s, Tuning(), NoBlobs(), None, needs,
                  embedder=Counting(), detector=Boxes(boxes))
    p.gate.update = lambda image: MotionResult(True, 1.0, [(0, 0, 4, 4)])  # always something new moving
    return p


def embeds_by_track(p) -> dict[int, int]:
    return {tid: m.embeds for tid, m in p.memo.items()}


def test_a_busy_camera_embeds_a_few_tracks_a_frame_and_again_only_on_change(tmp_path):
    boxes = [("person", (20 + 95 * i, 100, 60, 150)) for i in range(10)]
    colours = [(30 + 20 * i, 200 - 15 * i, 60) for i in range(10)]
    p = perceiver(tmp_path, boxes, Needs(classes={"person"}, embed=True))
    cap = Tuning().embed.max_per_frame
    per_frame, before = [], {}
    for ts in range(0, 26):
        if ts == 8:
            colours[3] = (240, 240, 240)  # one person takes off a dark jacket
        p.perceive(Frame(picture(boxes, colours), 1000.0 + ts, ts))
        now = embeds_by_track(p)
        per_frame.append(sum(now.values()) - sum(before.values()))
        changed = {tid for tid in now if now[tid] != before.get(tid, 0)}
        if 1 <= ts <= 5:
            assert len(changed) == cap  # the newest first, cap a frame, never all ten at once
        before = now
        if ts == 6:
            assert sorted(now.values()) == [1] * 10  # everyone has been looked at once
        if ts in (6, 7, 9, 10):
            assert not changed  # the same clothes and not yet every_s: no new look
        if ts == 8:
            assert len(changed) == 1  # the one who changed, at once
    assert max(per_frame) <= cap
    assert sum(per_frame) < 10 * 26 / 3  # the old rule: every person every 3 s


def test_only_tracks_inside_the_watchs_zones_and_of_its_classes_are_embedded(tmp_path):
    left, right, car = ("person", (100, 100, 60, 150)), ("person", (800, 100, 60, 150)), ("car", (200, 200, 120, 80))
    watch = preset("looks_like_check", text="a person in red", not_text="a person not in red")
    watch = watch.model_copy(update={"zones": ["left"]})
    needs = needs_for([watch])
    assert needs.embed and needs.embed_zones == {"left"} and needs.embed_classes == {"person"}
    p = perceiver(tmp_path, [left, right, car], needs, [LEFT])
    for ts in range(6):
        p.perceive(Frame(picture([left, right, car], [(0, 0, 200)] * 3), 1000.0 + ts, ts))
    now = {t.id: t.box for t in p.tracker.tracks}
    by_box = {round(now[tid][0]): n for tid, n in embeds_by_track(p).items() if tid in now}
    assert by_box[100] >= 1 and by_box.get(800, 0) == 0 and by_box.get(200, 0) == 0
    anywhere = needs_for([preset("looks_like_check")])
    assert anywhere.embed_zones is None and anywhere.embed_classes == {"person"}
    zone_look = needs_for([Watch(name="gate open", conditions=[Condition(
        signal="zone_looks_like", zones=["left"], params={"text": "an open gate", "not_text": "a closed gate"})])])
    assert zone_look.embedder and not zone_look.embed  # a zone's look needs the model, not every person's crop
    cars = needs_for([Watch(name="red car", subject=Subject(classes=["car"], looks_like="a red car"),
                            conditions=[Condition(signal="present")])])
    assert cars.embed_classes == {"car"}

    def red(*conditions, combine="all"):
        return needs_for([Watch(name="red", subject=Subject(classes=["person"], looks_like="a person in red"),
                                conditions=list(conditions), combine=combine)]).embed_zones
    assert red(Condition(signal="in_zone", zones=["left"])) == {"left"}
    assert red(Condition(signal="dwell_s", zones=["left"], op=">=", value=30)) == {"left"}
    # a moment is one tick: the look must be known before the person reaches the zone or the line
    assert red(Condition(signal="entered", zones=["left"])) is None
    assert red(Condition(signal="crossed", zones=["gate line"])) is None
    assert red(Condition(signal="in_zone", zones=["left"]), Condition(signal="present"), combine="any") is None


def test_a_red_jacket_watch_still_fires_within_the_budget(tmp_path):
    """The red jacket case on the vtest frame, with eight other people in view: the watch still fires for the red
    jacket, never for the dark coat, and no frame embeds more than max_per_frame."""
    from watchful.vision.embed import OnnxEmbedder
    from watchful.vision.models import sample_path
    from watchful.vision.reason import Reasoner
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 50)
    image = cap.read()[1]
    red, dark = (505, 215, 36, 91), (668, 257, 47, 110)
    others = [(30 + 55 * i, 300, 34, 90) for i in range(8)]
    boxes = [("person", b) for b in [red, dark, *others]]
    s = Settings(session_secret="y", data_dir=tmp_path, models_dir="models", _env_file=None)
    embedder = OnnxEmbedder("siglip2_b32", s)
    watch = preset("looks_like_check", text="a person wearing a red jacket", not_text="a person wearing a dark coat")
    camera = Camera("cam_r", "Plaza", "stream", None)
    p = Perceiver(camera, s, Tuning(), NoBlobs(), None, needs_for([watch]), embedder=embedder, detector=Boxes(boxes))
    p.gate.update = lambda img: MotionResult(True, 1.0, [(0, 0, 4, 4)])
    reasoner, engine = Reasoner(camera, Tuning()), RuleEngine([("w", watch)], [], Tuning(), s.timezone, "stream",
                                                                 embedder)
    fired, most = [], 0
    for ts in range(8):
        before = sum(m.embeds for m in p.memo.values())
        tick = reasoner.step(p.perceive(Frame(image, 2000.0 + ts, ts)))
        most = max(most, sum(m.embeds for m in p.memo.values()) - before)
        fired += [c.track_id for c in engine.on_tick("cam_r", tick, now=tick.ts)]
    embedder.release_text(0)
    at = {round(s.box[0]): s.track_id for s in tick.tracks}
    assert at[red[0]] in fired and at[dark[0]] not in fired  # the red jacket fires, the dark coat never
    assert most <= Tuning().embed.max_per_frame
