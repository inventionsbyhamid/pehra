import cv2
import numpy as np

from watchful.vision.models import sample_path
from watchful.vision.quality import best_crop, brighten, is_dark, sharpness


def frame():
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    return cap.read()[1]


def test_blurred_is_less_sharp():
    img = frame()
    assert sharpness(cv2.GaussianBlur(img, (15, 15), 0)) < 0.3 * sharpness(img)


def test_dark_frame_detected_and_brightened():
    dark = (frame() * 0.15).astype(np.uint8)
    assert is_dark(dark) and not is_dark(frame())
    out = brighten(dark)
    v_in, v_out = cv2.cvtColor(dark, cv2.COLOR_BGR2HSV)[..., 2], cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 2]
    assert v_out.mean() > 1.5 * v_in.mean()
    assert (v_out == 255).mean() < 0.05


def test_best_crop_prefers_sharp_and_big():
    img = frame()[100:300, 300:400]
    blurred = cv2.GaussianBlur(img, (15, 15), 0)
    assert best_crop([(blurred, 2.0), (img, 1.0)]) == 1
