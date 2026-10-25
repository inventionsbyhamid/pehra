import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.config import Settings
from watchful.registry import register
from watchful.tuning import FaceTuning
from watchful.vision.models import model_path


class ConsentRequired(Exception):
    pass


class NoFaceFound(Exception):
    pass


@dataclass
class Enrollment:
    person_id: str
    name: str
    feature: list[float]
    consent_ts: float = field(default_factory=time.time)
    thumbnail_key: str | None = None
    workspace_id: str = "default"  # store.db.DEFAULT_WORKSPACE


@dataclass
class FaceMatch:
    person_id: str
    name: str
    score: float


@register("faces", "yunet_sface")
class FaceIndex:
    """YuNet finds faces, SFace compares them with people the owner enrolled with consent."""

    def __init__(self, name: str = "yunet_sface", cfg: FaceTuning = FaceTuning(), settings: Settings | None = None):
        self.cfg, self.settings = cfg, settings
        self.threshold = cfg.cosine
        self.detector = cv2.FaceDetectorYN.create(str(model_path("yunet", settings)), "", (320, 320),
                                                  cfg.detect_score)
        self.recognizer = cv2.FaceRecognizerSF.create(str(model_path("sface", settings)), "")
        self._people: dict[str, Enrollment] = {}

    def has_face(self, image: np.ndarray) -> bool:
        return len(self._faces(image)) > 0

    def _faces(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(image)
        return np.zeros((0, 15), np.float32) if faces is None else faces

    def _feature(self, image: np.ndarray, face: np.ndarray) -> np.ndarray:
        aligned = self.recognizer.alignCrop(image, face)
        return self.recognizer.feature(aligned)

    def enroll(self, person_id: str, name: str, image: np.ndarray, consent: bool,
               thumbnail_key: str | None = None) -> Enrollment:
        if not consent:
            raise ConsentRequired("consent is required")
        faces = self._faces(image)
        if len(faces) == 0:
            raise NoFaceFound("no face found")
        face = max(faces, key=lambda f: f[2] * f[3])
        feature = self._feature(image, face).flatten().tolist()
        e = Enrollment(person_id, name, feature, thumbnail_key=thumbnail_key)
        self._people[person_id] = e
        return e

    def load(self, enrollments: list[Enrollment]) -> None:
        self._people = {e.person_id: e for e in enrollments}

    def remove(self, person_id: str) -> None:
        self._people.pop(person_id, None)

    def people(self) -> list[Enrollment]:
        return list(self._people.values())

    def identify(self, image: np.ndarray, box) -> FaceMatch | None:
        if not self._people:
            return None
        x, y, w, h = box
        H, W = image.shape[:2]
        x0, y0 = int(max(0, x)), int(max(0, y))
        x1, y1 = int(min(W, x + w)), int(min(H, y + self.cfg.region * h))
        if x1 - x0 < 10 or y1 - y0 < 10:
            return None
        crop = image[y0:y1, x0:x1]
        faces = self._faces(crop)
        if len(faces) == 0:
            return None
        feature = self._feature(crop, max(faces, key=lambda f: f[2] * f[3]))
        best = None
        for e in self._people.values():
            ref = np.array(e.feature, np.float32).reshape(1, -1)
            score = float(self.recognizer.match(feature, ref, cv2.FaceRecognizerSF_FR_COSINE))
            if score >= self.threshold and (best is None or score > best.score):
                best = FaceMatch(e.person_id, e.name, score)
        return best
