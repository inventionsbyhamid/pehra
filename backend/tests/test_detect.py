import time

import cv2
import pytest

from watchful.vision.detect import YoloxDetector as Detector
from watchful.vision.models import sample_path

MODELS = ["yolox_nano", "yolox_nano_320", "yolox_tiny", "yolox_s"]


def vtest_frame(n=50):
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    cap.set(cv2.CAP_PROP_POS_FRAMES, n)
    ok, img = cap.read()
    assert ok
    return img


@pytest.mark.parametrize("model", MODELS)
def test_detects_people_in_vtest(model):
    dets = Detector(model).detect(vtest_frame())
    people = [d for d in dets if d.cls == "person" and d.score >= 0.4]
    assert len(people) >= 2


@pytest.mark.parametrize("model", MODELS)
def test_filters_classes(model):
    # vtest has a parked van and car, but no bus or dog
    assert Detector(model, classes={"bus", "dog"}).detect(vtest_frame()) == []


@pytest.mark.parametrize("model", MODELS)
def test_boxes_inside_frame(model):
    img = vtest_frame()
    h, w = img.shape[:2]
    for d in Detector(model).detect(img):
        x, y, bw, bh = d.box
        assert 0 <= x and 0 <= y and x + bw <= w + 0.5 and y + bh <= h + 0.5 and bw > 0 and bh > 0


@pytest.mark.parametrize("model", MODELS)
def test_speed_report(model):
    cv2.setNumThreads(1)
    det, img = Detector(model), vtest_frame()
    det.detect(img)
    t0 = time.perf_counter()
    for _ in range(5):
        det.detect(img)
    print(f"\n{model}: {(time.perf_counter() - t0) / 5 * 1000:.1f} ms per frame, 1 thread")
    cv2.setNumThreads(-1)
