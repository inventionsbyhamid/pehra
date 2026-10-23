# Pose landmarks with the OpenCV Model Zoo MediaPipe pose model (Apache 2.0); input and output
# handling follows the Zoo's mp_pose.py. The region of interest comes from the track box, and a
# box wider than tall is tried turned both ways, keeping the more confident result.
from dataclasses import dataclass

import cv2
import numpy as np

from watchful.config import Settings
from watchful.vision.models import load_net

INPUT = 256
ENLARGE = 1.25
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24
L_WRIST, R_WRIST, NOSE = 15, 16, 0


@dataclass
class PoseResult:
    landmarks: np.ndarray | None  # (39, 5): x, y in frame pixels, z, visibility, presence
    torso_angle_deg: float
    hip_y: float
    body_height: float
    confidence: float


def torso_signals(lm: np.ndarray) -> tuple[float, float, float]:
    shoulders = (lm[L_SHOULDER, :2] + lm[R_SHOULDER, :2]) / 2
    hips = (lm[L_HIP, :2] + lm[R_HIP, :2]) / 2
    v = shoulders - hips
    length = float(np.linalg.norm(v)) or 1.0
    angle = float(np.degrees(np.arccos(np.clip(-v[1] / length, -1, 1))))
    return angle, float(hips[1]), 3.3 * length


class PoseEstimator:
    def __init__(self, conf: float = 0.5, settings: Settings | None = None):
        self.net = load_net("pose", settings)
        self.names = self.net.getUnconnectedOutLayersNames()
        self.conf = conf

    def _run(self, image: np.ndarray, center: tuple[float, float], side: float, angle: float):
        m = cv2.getRotationMatrix2D(center, angle, INPUT / side)
        m[:, 2] += (INPUT / 2 - center[0], INPUT / 2 - center[1])
        crop = cv2.warpAffine(image, m, (INPUT, INPUT), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT)
        blob = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        self.net.setInput(blob)
        landmarks, conf = self.net.forward(self.names)[:2]
        conf = float(conf[0][0])
        lm = landmarks[0].reshape(-1, 5).copy()
        lm[:, 3:] = 1 / (1 + np.exp(-lm[:, 3:]))
        inv = cv2.invertAffineTransform(m)
        pts = np.c_[lm[:, :2], np.ones(len(lm))] @ inv.T
        lm[:, :2] = pts
        return conf, lm

    def estimate(self, image: np.ndarray, box) -> PoseResult | None:
        x, y, w, h = box
        center = (x + w / 2, y + h / 2)
        side = max(w, h) * ENLARGE
        angles = (90.0, -90.0) if w > h else (0.0,)
        conf, lm = max((self._run(image, center, side, a) for a in angles), key=lambda r: r[0])
        if conf < self.conf:
            return None
        angle, hip_y, body_height = torso_signals(lm)
        return PoseResult(lm, angle, hip_y, body_height, conf)
