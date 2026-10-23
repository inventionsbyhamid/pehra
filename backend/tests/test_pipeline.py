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
