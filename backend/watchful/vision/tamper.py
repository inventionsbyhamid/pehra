from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from watchful.tuning import TamperTuning
from watchful.vision.quality import sharpness

Kind = Literal["covered", "blurred", "moved"]


@dataclass
class TamperEvent:
    kind: Literal["covered", "blurred", "moved", "restored"]
    ts: float
    detail: str


@dataclass
class _Reference:
    gray: np.ndarray
    keypoints: tuple
    descriptors: np.ndarray | None
    sharpness: float
    ts: float


class TamperMonitor:
    """Notices a covered, blurred or turned camera by comparing with a reference view."""

    def __init__(self, cfg: TamperTuning = TamperTuning()):
        self.cfg = cfg
        self.orb = cv2.ORB_create(cfg.orb_features)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.ref: _Reference | None = None
        self.problem: Kind | None = None
        self._pending: tuple[Kind, float] | None = None
        self._last_check = float("-inf")

    def update(self, image: np.ndarray, ts: float) -> TamperEvent | None:
        if ts - self._last_check < self.cfg.check_every_s:
            return None
        self._last_check = ts
        h, w = image.shape[:2]
        scale = self.cfg.width / w
        small = cv2.resize(image, (self.cfg.width, round(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        kind, detail = self._assess(gray)

        if kind is None:
            self._pending = None
            if self.problem:
                self.problem = None
                return TamperEvent("restored", ts, "the view is back to normal")
            if self.ref is None or ts - self.ref.ts >= self.cfg.reference_refresh_s:
                self.ref = self._reference(gray, ts)
            return None
        if kind == self.problem:
            return None
        if self._pending is None or self._pending[0] != kind:
            self._pending = (kind, ts)
        if ts - self._pending[1] >= self.cfg.hold_s:
            self.problem = kind
            return TamperEvent(kind, ts, detail)
        return None

    def _reference(self, gray: np.ndarray, ts: float) -> _Reference:
        kp, des = self.orb.detectAndCompute(gray, None)
        return _Reference(gray, kp, des, sharpness(gray), ts)

    def _assess(self, gray: np.ndarray) -> tuple[Kind | None, str]:
        c = self.cfg
        mean, std = cv2.meanStdDev(gray)
        if mean[0][0] < c.covered_mean or std[0][0] < c.covered_std:
            return "covered", "the picture is dark or flat, as if the lens is covered"
        if self.ref is None:
            return None, ""
        sharp = sharpness(gray)
        if sharp < c.blur_ratio * self.ref.sharpness:
            return "blurred", f"sharpness fell to {sharp / self.ref.sharpness:.0%} of normal"
        kp, des = self.orb.detectAndCompute(gray, None)
        if des is None or self.ref.descriptors is None:
            return None, ""
        pairs = self.matcher.knnMatch(self.ref.descriptors, des, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < c.ratio_test * p[1].distance]
        if len(good) < c.min_matches:
            return "moved", "the view no longer matches the usual scene"
        src = np.float32([self.ref.keypoints[m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        H, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 3.0)
        if H is None or mask.sum() / len(good) < c.min_inlier_share:
            return "moved", "the view no longer matches the usual scene"
        gh, gw = gray.shape
        center = np.float32([[[gw / 2, gh / 2]]])
        shift = float(np.linalg.norm(cv2.perspectiveTransform(center, H) - center)) / gw
        if shift > c.max_shift:
            return "moved", f"the view shifted by {shift:.0%} of its width"
        return None, ""
