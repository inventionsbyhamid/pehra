import cv2
import numpy as np
import pytest

from watchful.vision.embed import OnnxEmbedder as Embedder
from watchful.vision.models import sample_path

RED_TOP = (505, 215, 36, 91)
DARK_COAT = (668, 257, 47, 110)


@pytest.fixture(scope="module")
def emb():
    return Embedder("siglip2")


def crop(box):
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 50)
    img = cap.read()[1]
    x, y, w, h = box
    return img[y:y + h, x:x + w]


def test_text_matches_right_crop(emb):
    q = emb.text("a person wearing a red jacket")
    assert emb.similarity(emb.image(crop(RED_TOP)), q) > emb.similarity(emb.image(crop(DARK_COAT)), q)


def test_match_probability_is_calibrated(emb):
    q = emb.text("a person wearing a red jacket")
    right = emb.probability(emb.similarity(emb.image(crop(RED_TOP)), q))
    wrong = emb.probability(emb.similarity(emb.image(crop(DARK_COAT)), emb.text("a white van parked by a building")))
    assert 0 < wrong < right < 1 and wrong < 0.05


def test_embedding_normalized(emb):
    assert abs(np.linalg.norm(emb.image(crop(RED_TOP))) - 1) < 1e-4
    assert abs(np.linalg.norm(emb.text("a dog")) - 1) < 1e-4
