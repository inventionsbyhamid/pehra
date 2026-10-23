import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.config import Settings
from watchful.ingest.sources import Frame
from watchful.store.blobs import BlobStore
from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.appearance import signature
from watchful.vision.detect import Detector
from watchful.vision.faces import FaceIndex, FaceMatch
from watchful.vision.motion import MotionGate
from watchful.vision.pose import PoseEstimator, PoseResult
from watchful.vision.quality import brighten, is_dark, sharpness
from watchful.vision.tamper import TamperEvent, TamperMonitor
from watchful.vision.track import Track, Tracker


@dataclass
class Needs:
    """What the active watches need, so nothing heavy runs without a reason."""
    classes: set[str] | None = None  # None means all
    pose: bool = False
    faces: bool = False
    embed: bool = False


@dataclass
class Observation:
    ts: float
    track_id: int
    cls: str
    box: tuple[float, float, float, float]
    score: float
    pose: PoseResult | None = None  # only when freshly estimated on this frame
    face: FaceMatch | None = None
    signature: np.ndarray | None = None
    keyframe_key: str | None = None
    best_key: str | None = None
    embedding: np.ndarray | None = None
    started: bool = False
    ended: bool = False


@dataclass
class Perception:
    ts: float
    size: tuple[int, int]
    moving: bool
    observations: list[Observation]
    tamper: TamperEvent | None = None


@dataclass
class _TrackMemo:
    pose_ts: float = float("-inf")
    face_ts: float = float("-inf")
    face: FaceMatch | None = None
    keyframe_ts: float = float("-inf")
    keyframe_key: str | None = None
    best_key: str | None = None
    best_score: float = -1.0
    best_crop: np.ndarray | None = field(default=None, repr=False)
    signature: np.ndarray | None = None
    embed_ts: float = float("-inf")
    embeds: int = 0
    embedding: np.ndarray | None = None


def crop(image: np.ndarray, box) -> np.ndarray:
    x, y, w, h = box
    H, W = image.shape[:2]
    x0, y0 = int(max(0, x)), int(max(0, y))
    x1, y1 = int(min(W, x + w)), int(min(H, y + h))
    return image[y0:y1, x0:x1]


class Perceiver:
    """The heavy per frame work: night mode, tamper, motion, detection, tracking, pose, faces, keyframes."""

    def __init__(self, camera: Camera, settings: Settings, tuning: Tuning, blobs: BlobStore,
                 faces: FaceIndex | None, needs: Needs, embedder=None):
        self.camera, self.settings, self.t, self.blobs = camera, settings, tuning, blobs
        self.faces, self.embedder = faces, embedder
        self.gate = MotionGate(tuning.motion)
        self.detector = Detector(settings.detector, needs.classes, tuning.detect, settings)
        self.tracker = Tracker(tuning.track)
        self.tamper = TamperMonitor(tuning.tamper)
        self.pose = None
        self.memo: dict[int, _TrackMemo] = {}
        self.counts = {"frames": 0, "frames_skipped": 0, "detector_runs": 0, "pose_runs": 0, "face_runs": 0,
                       "embeds": 0, "ms_total": 0.0}
        self.set_needs(needs)

    def set_needs(self, needs: Needs) -> None:
        self.needs = needs
        self.detector.set_classes(None if needs.classes is None else needs.classes | {"person"})
        if needs.pose and self.pose is None:
            self.pose = PoseEstimator(self.t.pose, self.settings)

    def stats(self) -> dict:
        c = dict(self.counts)
        c["ms_per_frame"] = c.pop("ms_total") / max(c["frames"], 1)
        return c

    def perceive(self, frame: Frame) -> Perception:
        start = time.perf_counter()
        image, ts = frame.image, frame.ts
        self.counts["frames"] += 1
        mode = self.settings.night_mode
        work = brighten(image, self.t.quality) if mode == "on" or (mode == "auto" and is_dark(image, self.t.quality)) \
            else image
        tamper = self.tamper.update(image, ts)
        motion = self.gate.update(work)
        observations: list[Observation] = []
        if motion.moving or self.tracker.tracks:
            self.counts["detector_runs"] += 1
            up = self.tracker.update(self.detector.detect(work), ts)
            started = {t.id for t in up.started}
            for t in up.active:
                observations.append(self._observe(work, t, ts, t.id in started))
            for t in up.ended:
                m = self.memo.pop(t.id, _TrackMemo())
                observations.append(Observation(ts, t.id, t.cls, t.box, t.score, face=m.face, signature=m.signature,
                                                keyframe_key=m.keyframe_key, best_key=m.best_key,
                                                embedding=m.embedding, ended=True))
        else:
            self.counts["frames_skipped"] += 1
        self.counts["ms_total"] += (time.perf_counter() - start) * 1000
        h, w = image.shape[:2]
        return Perception(ts, (w, h), motion.moving, observations, tamper)

    def _observe(self, image: np.ndarray, t: Track, ts: float, started: bool) -> Observation:
        m = self.memo.setdefault(t.id, _TrackMemo())
        person = t.cls == "person"
        obs = Observation(ts, t.id, t.cls, t.box, t.score, started=started)
        if person and self.pose and self.needs.pose and ts - m.pose_ts >= 1 / self.t.pose.rate_hz:
            m.pose_ts = ts
            self.counts["pose_runs"] += 1
            obs.pose = self.pose.estimate(image, t.box)
        if person and self.faces and self.needs.faces and m.face is None \
                and ts - m.face_ts >= 1 / self.t.faces.rate_hz:
            m.face_ts = ts
            self.counts["face_runs"] += 1
            m.face = self.faces.identify(image, t.box)
        if started or ts - m.keyframe_ts >= self.t.perceive.keyframe_every_s:
            self._keyframe(image, t, ts, m)
        if person and self.embedder and self.needs.embed and m.best_crop is not None \
                and m.embeds < self.t.perceive.max_embeds_per_track and ts - m.embed_ts >= self.t.perceive.embed_every_s:
            m.embed_ts = ts
            m.embeds += 1
            self.counts["embeds"] += 1
            m.embedding = self.embedder.image(m.best_crop)
        obs.face, obs.signature, obs.embedding = m.face, m.signature, m.embedding
        obs.keyframe_key, obs.best_key = m.keyframe_key, m.best_key
        return obs

    def _keyframe(self, image: np.ndarray, t: Track, ts: float, m: _TrackMemo) -> None:
        c = crop(image, t.box)
        if c.size == 0:
            return
        m.keyframe_ts = ts
        key = f"{self.camera.id}/kf/{t.id}_{int(ts * 1000)}.jpg"
        ok, jpg = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, self.t.perceive.jpeg_quality])
        if ok:
            self.blobs.put(key, jpg.tobytes(), "image/jpeg")
            m.keyframe_key = key
        if t.cls == "person":
            m.signature = signature(image, t.box, self.t.appearance)
        score = sharpness(c) * c.shape[0] * c.shape[1]
        if score > m.best_score:
            m.best_score, m.best_key, m.best_crop = score, m.keyframe_key, c.copy()
