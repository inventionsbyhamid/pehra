# YOLOX pre and post processing adapted from the OpenCV Model Zoo demo (object_detection_yolox)
# and YOLOX demo/ONNXRuntime/onnx_inference.py, both Apache 2.0.
from dataclasses import dataclass

import cv2
import numpy as np

from watchful.config import Settings
from watchful.vision.models import load_net

COCO = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]
WATCH_CLASSES = {"person", "bicycle", "car", "motorcycle", "bus", "truck", "backpack", "handbag",
                 "suitcase", "dog", "cat"}

# input size and channel order per model, as each source's own demo feeds it
MODEL_INPUT = {"yolox_tiny": (416, False), "yolox_s": (640, True)}
STRIDES = (8, 16, 32)


@dataclass
class Detection:
    cls: str
    score: float
    box: tuple[float, float, float, float]


def _grids(size: int) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for s in STRIDES:
        n = size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


class Detector:
    def __init__(self, model: str = "yolox_tiny", conf: float = 0.4, nms: float = 0.45,
                 classes: set[str] = WATCH_CLASSES, settings: Settings | None = None):
        self.size, self.rgb = MODEL_INPUT[model]
        self.net = load_net(model, settings)
        self.conf, self.nms = conf, nms
        self.class_ids = np.array([i for i, c in enumerate(COCO) if c in classes], dtype=np.int64)
        self.grids, self.strides = _grids(self.size)

    def detect(self, image: np.ndarray) -> list[Detection]:
        if len(self.class_ids) == 0:
            return []
        h, w = image.shape[:2]
        ratio = min(self.size / h, self.size / w)
        resized = cv2.resize(image, (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
        padded = np.full((self.size, self.size, 3), 114, np.uint8)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        if self.rgb:
            padded = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
        self.net.setInput(blob)
        out = self.net.forward()[0]

        xy = (out[:, :2] + self.grids) * self.strides
        wh = np.exp(out[:, 2:4]) * self.strides
        scores = out[:, 4:5] * out[:, 5:]
        scores = scores[:, self.class_ids]
        best = scores.argmax(1)
        best_score = scores[np.arange(len(scores)), best]
        keep = best_score >= self.conf
        if not keep.any():
            return []
        xy, wh, best, best_score = xy[keep], wh[keep], best[keep], best_score[keep]
        boxes = np.concatenate([xy - wh / 2, wh], 1) / ratio
        x0 = np.clip(boxes[:, 0], 0, w)
        y0 = np.clip(boxes[:, 1], 0, h)
        x1 = np.clip(boxes[:, 0] + boxes[:, 2], 0, w)
        y1 = np.clip(boxes[:, 1] + boxes[:, 3], 0, h)
        boxes = np.stack([x0, y0, x1 - x0, y1 - y0], 1)
        cls_ids = self.class_ids[best]
        idx = cv2.dnn.NMSBoxesBatched(boxes.tolist(), best_score.tolist(), cls_ids.tolist(),
                                      self.conf, self.nms)
        dets = []
        for i in np.array(idx).flatten():
            bx = boxes[i]
            if bx[2] <= 0 or bx[3] <= 0:
                continue
            dets.append(Detection(COCO[cls_ids[i]], float(best_score[i]), tuple(float(v) for v in bx)))
        return dets
