import cv2
import numpy as np
import pytest

from watchful.vision.embed import OnnxEmbedder as Embedder
from watchful.vision.models import sample_path

RED_TOP = (505, 215, 36, 91)
DARK_COAT = (668, 257, 47, 110)


@pytest.fixture(scope="module", params=["siglip2", "siglip2_b32"])
def emb(request):
    return Embedder(request.param)


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


def test_each_tower_loads_only_when_used(monkeypatch):
    """The app makes watch texts with the text tower alone; a camera process embeds crops with the image tower alone."""
    import watchful.vision.embed as embed
    read = []

    class Net:
        def setInput(self, *a):
            pass

        def forward(self, *a):
            return np.ones((1, 4), np.float32)

    class Tokens:
        def encode(self, text):
            return type("Encoded", (), {"ids": [1, 2]})()

    monkeypatch.setattr(embed.cv2.dnn, "readNetFromONNX",
                        lambda path, *a: read.append(path.rsplit("/", 1)[-1]) or Net())
    monkeypatch.setattr(embed.Tokenizer, "from_file", staticmethod(lambda path: Tokens()))
    e = Embedder("siglip2_b32")
    assert read == []
    e.text("a man in red")
    assert read == ["text.onnx"]
    e.image(np.zeros((32, 32, 3), np.uint8))
    assert read == ["text.onnx", "vision.onnx"]
