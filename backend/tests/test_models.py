import hashlib
from pathlib import Path

import cv2
import pytest

from watchful.vision.models import MODEL_FILES, load_net, model_path, sample_path

DNN_MODELS = ["yolox_nano", "yolox_tiny", "yolox_s", "pose"]


@pytest.mark.parametrize("name", DNN_MODELS)
def test_all_models_load(name):
    net = load_net(name)
    assert len(net.getLayerNames()) > 0


def test_every_model_has_a_license():
    assert all(m.license for m in MODEL_FILES.values())


@pytest.mark.parametrize("name", ["yolox_nano_320", "yolox_nano_384"])
def test_our_exports_ship_in_the_repo(name):
    """A fresh clone builds without any release of ours: the exported detectors are in the repo, checked by sha256."""
    m = MODEL_FILES[name]
    assert m.url.startswith("repo:")
    path = Path(__file__).resolve().parents[2] / m.url.removeprefix("repo:")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == m.sha256


def test_face_models_create():
    det = cv2.FaceDetectorYN.create(str(model_path("yunet")), "", (320, 320))
    rec = cv2.FaceRecognizerSF.create(str(model_path("sface")), "")
    assert det is not None and rec is not None


def test_sample_clip_reads():
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    count = 0
    while cap.grab():
        count += 1
    assert count >= 100
