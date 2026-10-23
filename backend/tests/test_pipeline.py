import numpy as np
import pytest

from watchful.config import Settings
from watchful.ingest.sources import Frame, FileSource
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import Camera, SqliteRepository
from watchful.tuning import Tuning
from watchful.vision.faces import FaceIndex
from watchful.vision.models import sample_path
from watchful.vision.perceive import Needs
from watchful.vision.pipeline import CameraPipeline
from watchful.vision.zones import Zone


@pytest.fixture
def setup(tmp_path):
    settings = Settings(demo_password="x", session_secret="y", _env_file=None)
    cam = Camera("cam1", "Plaza", "file", None, [Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])])
    repo = SqliteRepository(tmp_path / "w.db")
    blobs = LocalBlobStore(tmp_path)
    return settings, cam, repo, blobs


def test_vtest_zone_events(setup):
    settings, cam, repo, blobs = setup
    pipe = CameraPipeline(cam, repo, blobs, FaceIndex(), settings, Tuning(), Needs(classes={"person"}))
    src = FileSource(sample_path("vtest.avi"), fps=5, start_epoch=0)
    while (f := src.read()) is not None and f.ts < 40:
        pipe.process(f)
    events = repo.query_events(camera_id="cam1")
    enters = [e for e in events if e.kind == "enter"]
    exits = [e for e in events if e.kind == "exit"]
    assert len(enters) >= 3 and len(exits) >= 2
    assert all(e.keyframe_key and blobs.get(e.keyframe_key) for e in enters + exits)
    assert pipe.stats()["frames"] > 150


def test_idle_frames_skip_detector(setup):
    settings, cam, repo, blobs = setup
    pipe = CameraPipeline(cam, repo, blobs, FaceIndex(), settings, Tuning(), Needs(classes={"person"}))
    img = np.full((360, 640, 3), 90, np.uint8)
    for i in range(30):
        pipe.process(Frame(img, i * 0.2, i))
    assert pipe.stats()["detector_runs"] <= 3


class StubDetector:
    name = "stub"

    def __init__(self):
        self.calls = 0
        self.boxes = []

    def set_classes(self, classes):
        pass

    def detect(self, image):
        from watchful.vision.detect import Detection
        self.calls += 1
        return [Detection("person", 0.9, b) for b in self.boxes]


def test_detector_stride_and_new_motion(setup):
    from watchful.vision.perceive import Perceiver
    settings, cam, repo, blobs = setup
    det = StubDetector()
    p = Perceiver(cam, settings, Tuning(), blobs, None, Needs(classes={"person"}), detector=det)
    base = np.full((360, 640, 3), 40, np.uint8)
    for i in range(10):
        p.perceive(Frame(base.copy(), i * 0.2, i))
    calls_before = det.calls
    for i in range(10, 30):
        img = base.copy()
        x = 100 + 2 * (i - 10)
        img[100:220, x:x + 50] = 230
        det.boxes = [(float(x - 5), 95.0, 60.0, 130.0)]
        p.perceive(Frame(img, i * 0.2, i))
    walking = det.calls - calls_before
    assert 8 <= walking <= 13  # about every other frame while only the tracked person moves
    img = base.copy()
    img[100:220, 140:190] = 230
    img[200:300, 500:560] = 230  # someone new appears far from the track
    before = det.calls
    det.boxes = [(135.0, 95.0, 60.0, 130.0), (495.0, 195.0, 70.0, 110.0)]
    p.perceive(Frame(img, 30 * 0.2, 30))
    p.perceive(Frame(img, 31 * 0.2, 31))
    assert det.calls - before >= 1
