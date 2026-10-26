"""The tracker at the frame rates a shared camera process delivers: on the Plaza clip, line crossings and track ids at
1 analysed frame a second match those at 10 (each restarted id would be another enter and a lost crossing)."""
import cv2
import pytest

from watchful.components import make_detector
from watchful.config import Settings
from watchful.ingest.sources import Frame
from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.models import sample_path
from watchful.vision.perceive import Needs
from watchful.vision.pipeline import CameraPipeline
from watchful.vision.zones import Zone

SECONDS = 75.0
LINE = Zone("l1", "mid line", [(0.5, 0.0), (0.5, 1.0)], "line")
WALKWAY = Zone("z1", "left walkway", [(0, 0.3), (0.5, 0.3), (0.5, 1), (0, 1)])


class Cached:
    """The detector once per frame index, shared by both runs, so they differ only in which frames they see."""

    def __init__(self, view):
        self.view, self.name, self.found, self.index = view, view.name, {}, 0

    def set_classes(self, classes):
        self.view.set_classes(classes)

    def detect(self, image):
        if self.index not in self.found:
            self.found[self.index] = self.view.detect(image)
        return self.found[self.index]


class Rows:
    def __init__(self):
        self.rows = []

    def add_events(self, rows):
        self.rows += rows


class NoBlobs:
    def put(self, *a):
        pass


@pytest.fixture(scope="module")
def detector(tmp_path_factory):
    s = Settings(session_secret="y", data_dir=tmp_path_factory.mktemp("plaza"), _env_file=None)
    return s, Cached(make_detector(s, Tuning()).view())


def run(s, detector, every: int) -> tuple[int, int]:
    cam = Camera("cam_p", "Plaza", "stream", None, [WALKWAY, LINE], workspace_id="ws_p")
    rows = Rows()
    pipe = CameraPipeline(cam, rows, NoBlobs(), None, s, Tuning(), Needs(classes={"person"}), detector=detector)
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    i = 0
    while i < SECONDS * 10:
        ok, image = cap.read()
        if not ok:
            break
        if i % every == 0:
            detector.index = i
            pipe.process(Frame(image, 1000.0 + i / 10, i, i / 10))
        i += 1
    cap.release()
    crossings = sum(1 for r in rows.rows if r.kind == "cross" and r.cls == "person")
    ids = len({r.track_id for r in rows.rows if r.cls == "person"})
    return crossings, ids


def test_crossings_and_ids_hold_at_one_frame_a_second(detector):
    threads = cv2.getNumThreads()
    cv2.setNumThreads(4)
    try:
        s, cached = detector
        crossings_10, ids_10 = run(s, cached, 1)
        crossings_1, ids_1 = run(s, cached, 10)
    finally:
        cv2.setNumThreads(threads)
    assert crossings_10 >= 15
    assert abs(crossings_1 - crossings_10) <= 0.15 * crossings_10, (crossings_1, crossings_10)
    assert ids_1 <= 1.5 * ids_10, (ids_1, ids_10)
