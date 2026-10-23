from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.tuning import MotionTuning


@dataclass
class MotionResult:
    moving: bool
    ratio: float
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)


class MotionGate:
    """MOG2 on a small copy of the frame; boxes come back in full frame pixels."""

    def __init__(self, cfg: MotionTuning = MotionTuning()):
        self.cfg = cfg
        self.sub = cv2.createBackgroundSubtractorMOG2(history=cfg.history, varThreshold=cfg.var_threshold,
                                                      detectShadows=False)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def update(self, image: np.ndarray) -> MotionResult:
        h, w = image.shape[:2]
        scale = min(1.0, self.cfg.gate_width / w)
        small = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        mask = self.sub.apply(small)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.dilate(mask, self.kernel, iterations=2)
        ratio = float(cv2.countNonZero(mask)) / mask.size
        min_area = self.cfg.min_box_ratio * mask.size
        boxes = []
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            if cv2.contourArea(c) < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            boxes.append((int(x / scale), int(y / scale), int(bw / scale), int(bh / scale)))
        return MotionResult(moving=ratio >= self.cfg.min_ratio and bool(boxes), ratio=ratio, boxes=boxes)
