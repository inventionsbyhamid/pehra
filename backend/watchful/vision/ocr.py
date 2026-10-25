"""Reading printed text (the OCR setting). Text lines are found by the Model Zoo's PP-OCRv3 DB detector through
cv2.dnn.TextDetectionModel_DB, then each line is read by a recogniser:

ppocr_v3det_v5rec  PP-OCRv5 mobile English recognition (any case, punctuation). Its output is 1 x T x C and
                   cv2.dnn.TextRecognitionModel returns an empty string for it, so it is decoded here.
ppocr_v3_crnn      the Zoo's CRNN through cv2.dnn.TextRecognitionModel: digits and lowercase letters only, the
                   fastest, and the one recogniser that runs on the classic engine.
fake               reads nothing (or the boxes it is given), for tests.
"""
import threading
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from watchful.config import Settings
from watchful.registry import register
from watchful.vision.models import model_path, read_net, vocabulary
from watchful.vision.quality import flatten, grey, order_quad

# Model Zoo PP-OCRv3 DB values: longest input side, pixel scale and mean, post processing
DET_SIDE = 736
DET_MEAN = (122.67891434, 116.66876762, 104.00698793)
DET_BINARY, DET_POLYGON, DET_MAX_BOXES, DET_UNCLIP = 0.3, 0.5, 200, 2.0
V5_HEIGHT, V5_MIN_WIDTH = 48, 320  # PP-OCRv5 recognition input: 48 px high, padded to at least 320 wide
CRNN_SIZE = (100, 32)


def load_net(name: str, settings: Settings | None = None, engine: str | None = None) -> cv2.dnn.Net:
    """A model by name on the settings' engine, or on `engine` when a caller knows better."""
    return read_net(model_path(name, settings), engine or (settings.dnn_engine if settings else "auto"))


@dataclass(frozen=True)
class TextBox:
    text: str
    quad: np.ndarray  # 4x2 float32 image pixels: top left, top right, bottom right, bottom left
    score: float  # 0..1, the recogniser's confidence when it gives one, else the detector's


class Ocr(Protocol):
    def read(self, image: np.ndarray) -> list[TextBox]: ...


def ctc_greedy(probs: np.ndarray, vocab: list[str]) -> tuple[str, float]:
    """Best path CTC decode of one T x C score matrix: the top class per step, repeats collapsed, the blank
    (class 0) dropped, class k read as vocab[k - 1]. Also returns the mean top score of the kept steps."""
    best = probs.argmax(1)
    keep = (best != 0) & np.r_[True, best[1:] != best[:-1]]
    text = "".join(vocab[k - 1] for k in best[keep])
    score = float(probs[np.arange(len(best)), best][keep].mean()) if keep.any() else 0.0
    return text, score


class _Detector:
    def __init__(self, settings: Settings | None):
        self.model = cv2.dnn.TextDetectionModel_DB(load_net("text_det", settings))
        self.model.setBinaryThreshold(DET_BINARY).setPolygonThreshold(DET_POLYGON)
        self.model.setMaxCandidates(DET_MAX_BOXES).setUnclipRatio(DET_UNCLIP)

    def detect(self, image: np.ndarray) -> list[tuple[np.ndarray, float]]:
        h, w = image.shape[:2]
        s = DET_SIDE / max(h, w)  # keep the aspect: sides rounded to the network's stride of 32
        size = (max(32, round(w * s / 32) * 32), max(32, round(h * s / 32) * 32))
        self.model.setInputParams(1 / 255, size, DET_MEAN)
        quads, confidences = self.model.detect(image)
        return [(order_quad(q), float(c)) for q, c in zip(quads, confidences)]


class _V5Recogniser:
    def __init__(self, settings: Settings | None):
        self.net = load_net("text_rec_v5", settings)
        self.vocab = vocabulary("text_rec_v5", settings)

    def __call__(self, crop: np.ndarray) -> tuple[str, float | None]:
        if crop.ndim == 2:
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        h, w = crop.shape[:2]
        nw = max(1, round(V5_HEIGHT * w / h))
        x = cv2.resize(crop, (nw, V5_HEIGHT)).astype(np.float32) / 127.5 - 1.0  # BGR, as PaddleOCR feeds it
        pad = np.zeros((V5_HEIGHT, max(V5_MIN_WIDTH, -(-nw // 8) * 8), 3), np.float32)
        pad[:, :nw] = x
        self.net.setInput(pad.transpose(2, 0, 1)[None])
        return ctc_greedy(self.net.forward()[0], self.vocab)


class _CrnnRecogniser:
    def __init__(self, settings: Settings | None):
        self.model = cv2.dnn.TextRecognitionModel(load_net("text_rec", settings, engine="classic"))
        self.model.setDecodeType("CTC-greedy")
        self.model.setVocabulary(vocabulary("text_rec"))
        self.model.setInputParams(1 / 127.5, CRNN_SIZE, (127.5, 127.5, 127.5))

    def __call__(self, crop: np.ndarray) -> tuple[str, float | None]:
        return self.model.recognize(grey(crop)), None


@register("ocr", "ppocr_v3det_v5rec", "ppocr_v3_crnn")
class PpOcr:
    def __init__(self, name: str = "ppocr_v3det_v5rec", settings: Settings | None = None, **_):
        self.detector = _Detector(settings)
        self.recognise = _V5Recogniser(settings) if name == "ppocr_v3det_v5rec" else _CrnnRecogniser(settings)
        self._lock = threading.Lock()

    def detect(self, image: np.ndarray) -> list[np.ndarray]:
        """Where lines of text are, without reading them."""
        with self._lock:
            return [quad for quad, _ in self.detector.detect(image)]

    def read_line(self, crop: np.ndarray) -> tuple[str, float]:
        """One upright line of text already cut out, and the recogniser's confidence (0.5 when it gives none)."""
        with self._lock:
            text, score = self.recognise(crop)
        return text.strip(), 0.5 if score is None else score

    def read(self, image: np.ndarray) -> list[TextBox]:
        out = []
        with self._lock:
            for quad, det_score in self.detector.detect(image):
                crop, _ = flatten(image, quad, interpolation=cv2.INTER_CUBIC)
                if crop.shape[0] > 1.5 * crop.shape[1]:  # a vertical line of text
                    crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
                text, score = self.recognise(crop)
                if text.strip():
                    out.append(TextBox(text.strip(), quad, det_score if score is None else score))
        return out


@register("ocr", "fake")
class FakeOcr:
    def __init__(self, boxes: list[TextBox] | None = None, **_):
        self.boxes = boxes or []

    def detect(self, image: np.ndarray) -> list[np.ndarray]:
        return [box.quad for box in self.boxes]

    def read_line(self, crop: np.ndarray) -> tuple[str, float]:
        return "", 0.0

    def read(self, image: np.ndarray) -> list[TextBox]:
        return list(self.boxes)
