import threading
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


class ManyFaces(Exception):
    pass


@dataclass
class Enrollment:
    person_id: str
    name: str
    feature: list[float]
    consent_ts: float = field(default_factory=time.time)
    thumbnail_key: str | None = None  # kept for rows from before photos were dropped; new enrolments keep none
    workspace_id: str | None = None  # set by the repository
    features: list[list[float]] | None = None  # every template (one per enrolment photo); feature is the first

    def templates(self) -> list[list[float]]:
        return self.features or [self.feature]


@dataclass
class FaceMatch:
    person_id: str
    name: str
    score: float
    margin: float = 1.0  # how much closer than the next enrolled person


def judge(looks: list[FaceMatch], cfg: FaceTuning) -> FaceMatch | None:
    """A track's name from its face looks so far: one look at name_single or more, or name_frames looks of the same
    person at name_multi or more, each at least name_margin ahead of the next person. Anything less (a possible
    match, from possible_low up) never names anyone."""
    if not looks:
        return None
    last = looks[-1]
    if last.score >= cfg.name_single:
        return last
    agree = [x for x in looks if x.person_id == last.person_id and x.score >= cfg.name_multi
             and x.margin >= cfg.name_margin]
    return last if len(agree) >= cfg.name_frames and last in agree else None


@register("faces", "yunet_sface")
class FaceIndex:
    """YuNet finds faces, SFace compares them with people the owner enrolled with consent."""

    def __init__(self, name: str = "yunet_sface", cfg: FaceTuning = FaceTuning(), settings: Settings | None = None):
        self.cfg, self.settings = cfg, settings
        self.threshold = cfg.cosine
        self.detector = cv2.FaceDetectorYN.create(str(model_path("yunet", settings)), "", (320, 320),
                                                  cfg.detect_score)
        self.recognizer = cv2.FaceRecognizerSF.create(str(model_path("sface", settings)), "")
        self.lock = threading.Lock()  # the networks keep per call state (input size): one caller at a time
        self._people: dict[str, Enrollment] = {}
        self.workspace_id: str | None = None

    def share(self, enrollments: list[Enrollment] | None = None, workspace_id: str | None = None) -> "FaceIndex":
        """Another index on the same two networks with its own people: a camera process keeps one per society it
        watches, so a face enrolled in one society is never named on another's camera. With `workspace_id` it
        refuses anyone enrolled in another society."""
        other = object.__new__(type(self))
        other.cfg, other.settings, other.threshold = self.cfg, self.settings, self.threshold
        other.detector, other.recognizer, other.lock = self.detector, self.recognizer, self.lock
        other.workspace_id, other._people = workspace_id, {}
        other.load(enrollments or [])
        return other

    def has_face(self, image: np.ndarray) -> bool:
        return len(self._faces(image)) > 0

    def _faces(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        with self.lock:
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(image)
        return np.zeros((0, 15), np.float32) if faces is None else faces

    def _feature(self, image: np.ndarray, face: np.ndarray) -> np.ndarray:
        with self.lock:
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

    def template(self, image: np.ndarray) -> list[float]:
        """The face template of the one face in an enrolment photo."""
        faces = self._faces(image)
        if len(faces) == 0:
            raise NoFaceFound("no face found")
        if len(faces) > 1:
            raise ManyFaces("more than one face")
        return self._feature(image, faces[0]).flatten().tolist()

    def similarity(self, a: list[float], b: list[float]) -> float:
        a, b = (np.array(v, np.float32).reshape(1, -1) for v in (a, b))
        with self.lock:
            return float(self.recognizer.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))

    def load(self, enrollments: list[Enrollment]) -> None:
        ws = getattr(self, "workspace_id", None)
        self._people = {e.person_id: e for e in enrollments if not ws or e.workspace_id in (None, ws)}

    def remove(self, person_id: str) -> None:
        self._people.pop(person_id, None)

    def people(self) -> list[Enrollment]:
        return list(self._people.values())

    def identify(self, image: np.ndarray, box, floor: float | None = None) -> FaceMatch | None:
        """The enrolled person whose closest template best matches the face in the top of `box`, at `floor` or more
        (the blur and search threshold by default), with its margin over the next person."""
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
        scores = []
        for e in list(self._people.values()):
            refs = [np.array(t, np.float32).reshape(1, -1) for t in e.templates()]
            with self.lock:
                best = max(float(self.recognizer.match(feature, r, cv2.FaceRecognizerSF_FR_COSINE)) for r in refs)
            scores.append((best, e))
        scores.sort(key=lambda x: -x[0])
        score, e = scores[0]
        if score < (self.threshold if floor is None else floor):
            return None
        return FaceMatch(e.person_id, e.name, score, score - scores[1][0] if len(scores) > 1 else score)
