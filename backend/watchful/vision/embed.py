from typing import Protocol

import cv2
import numpy as np
from tokenizers import Tokenizer

from watchful.config import Settings
from watchful.registry import register
from watchful.vision.models import EMBEDDERS, embedder_path


def _unit(v: np.ndarray) -> np.ndarray:
    v = v.reshape(-1).astype(np.float32)
    return v / (np.linalg.norm(v) or 1.0)


class Embedder(Protocol):
    def image(self, crop: np.ndarray) -> np.ndarray: ...

    def text(self, query: str) -> np.ndarray: ...


@register("embedder", *EMBEDDERS)
class OnnxEmbedder:
    """Images and text in one embedding space, both towers on the OpenCV DNN engine.
    The text tower is large, so it loads on first use."""

    def __init__(self, name: str = "siglip2", settings: Settings | None = None):
        self.spec = EMBEDDERS[name]
        self.name, self.settings = name, settings
        self.vision = cv2.dnn.readNetFromONNX(str(embedder_path(name, "vision.onnx", settings)),
                                              cv2.dnn.ENGINE_AUTO)
        self._text_net = None
        self._tokenizer = None

    def image(self, crop: np.ndarray) -> np.ndarray:
        s = self.spec
        x = cv2.resize(crop, (s.size, s.size), interpolation=cv2.INTER_LINEAR)
        x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = (x - s.mean) / s.std
        self.vision.setInput(x.transpose(2, 0, 1)[None])
        return _unit(self.vision.forward(s.output))

    def text(self, query: str) -> np.ndarray:
        s = self.spec
        if self._text_net is None:
            self._text_net = cv2.dnn.readNetFromONNX(str(embedder_path(self.name, "text.onnx", self.settings)),
                                                     cv2.dnn.ENGINE_AUTO)
            self._tokenizer = Tokenizer.from_file(str(embedder_path(self.name, "tokenizer.json", self.settings)))
        ids = self._tokenizer.encode(query.lower() if s.lowercase else query).ids[:s.max_tokens]
        ids = ids + [s.pad_id] * (s.max_tokens - len(ids))
        self._text_net.setInput(np.array([ids], np.int64), "input_ids")
        return _unit(self._text_net.forward(s.output))

    @staticmethod
    def similarity(a: np.ndarray, b: np.ndarray) -> float:
        return similarity(a, b)

    def probability(self, cosine: float) -> float:
        """How likely an image matches a text, from the model's own calibration (cosine when it has none)."""
        s = self.spec
        if s.logit_scale is None:
            return float(cosine)
        return float(1.0 / (1.0 + np.exp(-(np.exp(s.logit_scale) * cosine + s.logit_bias))))


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))
