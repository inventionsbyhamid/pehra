import cv2
import numpy as np

from watchful.vision.models import sample_path
from watchful.vision.tamper import TamperMonitor


def frame():
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    return cap.read()[1]


def noisy(img, seed):
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)


def run(frames):
    mon = TamperMonitor(check_every_s=1.0, hold_s=5.0)
    events = []
    for ts, img in enumerate(frames):
        e = mon.update(img, float(ts))
        if e:
            events.append(e)
    return events


def test_black_frames_covered():
    img = frame()
    events = run([noisy(img, i) for i in range(5)] + [np.full_like(img, 3)] * 8)
    assert [e.kind for e in events] == ["covered"]


def test_heavy_blur_blurred():
    img = frame()
    blur = cv2.GaussianBlur(img, (31, 31), 0)
    events = run([noisy(img, i) for i in range(5)] + [blur] * 8 + [img] * 3)
    assert [e.kind for e in events] == ["blurred", "restored"]


def test_shifted_view_moved():
    img = frame()
    h, w = img.shape[:2]
    shifted = cv2.warpAffine(img, np.float32([[1, 0, 0.15 * w], [0, 1, 0]]), (w, h))
    events = run([noisy(img, i) for i in range(5)] + [noisy(shifted, i) for i in range(8)])
    assert [e.kind for e in events] == ["moved"]


def test_normal_noise_no_event():
    img = frame()
    assert run([noisy(img, i) for i in range(15)]) == []
