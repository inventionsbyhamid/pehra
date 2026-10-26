import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.components import make_detector, make_pose
from watchful.config import Settings
from watchful.ingest.sources import Frame
from watchful.store.blobs import BlobStore, society_key
from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.appearance import same_person, signature
from watchful.vision.detect import Detector
from watchful.vision.faces import FaceIndex, FaceMatch, judge
from watchful.vision.motion import MotionGate
from watchful.vision.pose import PoseEstimator, PoseResult
from watchful.vision.quality import brighten, is_dark, sharpness
from watchful.vision.tamper import TamperEvent, TamperMonitor
from watchful.vision.zones import foot_point
from watchful.vision.track import Track, Tracker


@dataclass
class Needs:
    """What the active watches need, so nothing heavy runs without a reason."""
    classes: set[str] | None = None  # None means all
    pose: bool = False  # every person, for hands and climbing
    posture: bool = False  # only people whose box looks like a fall may be happening
    faces: bool = False
    embed: bool = False  # embed track crops (looks_like, a described subject, same_as)
    slow: bool = False  # detect every zones.slow_tick_s even when nothing moves, for parked or left things
    embedder: bool = False  # load the image model for zone pictures, without embedding every person
    embed_classes: set[str] = field(default_factory=lambda: {"person"})  # the classes those watches are about
    embed_zones: set[str] | None = None  # the zones they look in; None: anywhere


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
    attributes: dict[str, str] | None = None  # what readers attached, such as plate_text and plate_colour


@dataclass
class Perception:
    ts: float
    size: tuple[int, int]
    moving: bool
    observations: list[Observation]
    tamper: TamperEvent | None = None
    pos: float | None = None
    scene_key: str | None = None  # a whole frame stored for the search index


@dataclass
class _TrackMemo:
    pose_ts: float = float("-inf")
    last_box: tuple | None = None
    last_ts: float = 0.0
    suspicious_until: float = float("-inf")
    face_ts: float = float("-inf")
    face: FaceMatch | None = None  # the name, once judge() gives one
    looks: list[FaceMatch] = field(default_factory=list)  # the latest face looks, name_frames at most
    keyframe_ts: float = float("-inf")
    keyframe_key: str | None = None
    best_key: str | None = None
    best_score: float = -1.0
    best_crop: np.ndarray | None = field(default=None, repr=False)
    signature: np.ndarray | None = None
    embed_ts: float = float("-inf")
    embeds: int = 0
    embedding: np.ndarray | None = None
    embed_signature: np.ndarray | None = None  # the clothes when last embedded


def crop(image: np.ndarray, box) -> np.ndarray:
    x, y, w, h = box
    H, W = image.shape[:2]
    x0, y0 = int(max(0, x)), int(max(0, y))
    x1, y1 = int(min(W, x + w)), int(min(H, y + h))
    return image[y0:y1, x0:x1]


class Perceiver:
    """The heavy per frame work: night mode, tamper, motion, detection, tracking, pose, faces, keyframes."""

    def __init__(self, camera: Camera, settings: Settings, tuning: Tuning, blobs: BlobStore,
                 faces: FaceIndex | None, needs: Needs, embedder=None, detector: Detector | None = None,
                 pose: PoseEstimator | None = None, plates=None):
        self.camera, self.settings, self.t, self.blobs = camera, settings, tuning, blobs
        self.faces, self.embedder = faces, embedder
        self.plates = plates  # logs.reading.PlateSampler on a camera with plate reading on
        self.gate = MotionGate(tuning.motion)
        self.detector = detector or make_detector(settings, tuning, needs.classes)
        self.tracker = Tracker(tuning.track)
        self.tamper = TamperMonitor(tuning.tamper)
        self.pose = pose
        self.memo: dict[int, _TrackMemo] = {}
        self.counts = {"frames": 0, "frames_skipped": 0, "detector_runs": 0, "detector_skipped": 0, "pose_runs": 0,
                       "face_runs": 0, "embeds": 0, "ms_total": 0.0}
        self._since_detect = 0
        self._due: list[tuple] = []  # tracks this frame that want their crop embedded
        self.stride = max(1, tuning.detect.stride)  # a camera that must not miss a passing vehicle lowers it
        self._last_scene = float("-inf")
        self._last_slow = float("-inf")
        self.set_needs(needs)

    def set_needs(self, needs: Needs) -> None:
        self.needs = needs
        self.detector.set_classes(None if needs.classes is None else needs.classes | {"person"})
        if (needs.pose or needs.posture) and self.pose is None:
            self.pose = make_pose(self.settings, self.t)

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
        slow = self.needs.slow and ts - self._last_slow >= self.t.zones.slow_tick_s
        if slow:
            self._last_slow = ts
        if motion.moving or self.tracker.tracks or slow:
            tracks = self.tracker.tracks
            due = self._since_detect + 1 >= self.stride
            if not tracks or due or slow or self._new_motion(motion.boxes, tracks):
                self.counts["detector_runs"] += 1
                self._since_detect = 0
                dets = self.detector.detect(work)
            else:
                self.counts["detector_skipped"] += 1
                self._since_detect += 1
                dets = []
            up = self.tracker.update(dets, ts, work)
            started = {t.id for t in up.started}
            for t in up.active:
                obs = self._observe(work, t, ts, t.id in started)
                if self.plates is not None:
                    obs.attributes = self.plates.observe(image, t, ts)
                observations.append(obs)
            self._embed_due(work, ts)
            for t in up.ended:
                m = self.memo.pop(t.id, _TrackMemo())
                observations.append(Observation(ts, t.id, t.cls, t.box, t.score, face=m.face, signature=m.signature,
                                                keyframe_key=m.keyframe_key, best_key=m.best_key,
                                                embedding=m.embedding, ended=True,
                                                attributes=self.plates.forget(t.id, ts) if self.plates else None))
        else:
            self.counts["frames_skipped"] += 1
        self.counts["ms_total"] += (time.perf_counter() - start) * 1000
        h, w = image.shape[:2]
        return Perception(ts, (w, h), motion.moving, observations, tamper, frame.pos,
                          self._scene(image, ts, motion.moving))

    def _scene(self, image: np.ndarray, ts: float, moving: bool) -> str | None:
        every = self.t.perceive.scene_every_s if moving else self.t.perceive.scene_idle_every_s
        if ts - self._last_scene < every:
            return None
        self._last_scene = ts
        key = society_key(self.camera.workspace_id, self.camera.id, "scene", f"{int(ts * 1000)}.jpg")
        ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.t.perceive.jpeg_quality])
        if not ok:
            return None
        self.blobs.put(key, jpg.tobytes(), "image/jpeg")
        return key

    def _new_motion(self, boxes: list[tuple], tracks: list[Track]) -> bool:
        """True when some moving blob is not mostly inside a tracked object's box."""
        for mx, my, mw, mh in boxes:
            area = max(mw * mh, 1)
            covered = 0.0
            for t in tracks:
                tx, ty, tw, th = t.box
                iw = max(0.0, min(mx + mw, tx + tw) - max(mx, tx))
                ih = max(0.0, min(my + mh, ty + th) - max(my, ty))
                covered = max(covered, iw * ih / area)
            if covered < self.t.detect.known_motion_overlap:
                return True
        return False

    def finish(self, ts: float, pos: float | None = None, size: tuple[int, int] = (0, 0)) -> Perception:
        """Ends every open track, for the end of a file or a scan chunk."""
        ended = []
        for t in self.tracker.tracks:
            if t.started:
                m = self.memo.pop(t.id, _TrackMemo())
                ended.append(Observation(ts, t.id, t.cls, t.box, t.score, face=m.face, signature=m.signature,
                                         keyframe_key=m.keyframe_key, best_key=m.best_key,
                                         embedding=m.embedding, ended=True))
        self.tracker.tracks = []
        return Perception(ts, size, False, ended, None, pos)

    def _observe(self, image: np.ndarray, t: Track, ts: float, started: bool) -> Observation:
        m = self.memo.setdefault(t.id, _TrackMemo())
        person = t.cls == "person"
        obs = Observation(ts, t.id, t.cls, t.box, t.score, started=started)
        if person and self.pose and self._wants_pose(t, ts, m) and ts - m.pose_ts >= 1 / self.t.pose.rate_hz:
            m.pose_ts = ts
            self.counts["pose_runs"] += 1
            obs.pose = self.pose.estimate(image, t.box)
        if person and self.faces and self.needs.faces and m.face is None \
                and ts - m.face_ts >= 1 / self.t.faces.rate_hz:
            m.face_ts = ts
            self.counts["face_runs"] += 1
            look = self.faces.identify(image, t.box, floor=-1.0)  # every face found, however weak, breaks a streak
            if look is not None:
                look.ts = ts
                m.looks = (m.looks + [look])[-max(1, self.t.faces.name_frames):]
                m.face = judge(m.looks, self.t.faces)
        if started or ts - m.keyframe_ts >= self.t.perceive.keyframe_every_s:
            self._keyframe(image, t, ts, m)
        if self._wants_embed(image, t, ts, m):
            self._due.append((m.embeds > 0, m.embed_ts, t.id, t, m, obs))
        obs.face, obs.signature, obs.embedding = m.face, m.signature, m.embedding
        obs.keyframe_key, obs.best_key = m.keyframe_key, m.best_key
        return obs

    def _wants_embed(self, image: np.ndarray, t: Track, ts: float, m: _TrackMemo) -> bool:
        """Cheap tests first: a class and a place a watch can use, a stable box big enough, then due (never looked
        at, or every_s passed) or changed clothes."""
        n, c = self.needs, self.t.embed
        if not (self.embedder and n.embed) or t.cls not in n.embed_classes or min(t.box[2], t.box[3]) < c.min_side:
            return False
        if n.embed_zones is not None:
            size = (image.shape[1], image.shape[0])
            foot = foot_point(t.box)
            if not any(z.name in n.embed_zones and z.contains(foot, size) for z in self.camera.zones):
                return False
        if m.embeds == 0 or ts - m.embed_ts >= c.every_s or m.embed_signature is None:
            return True
        return same_person(m.embed_signature, signature(image, t.box, self.t.appearance)) >= c.change

    def _embed_due(self, image: np.ndarray, ts: float) -> None:
        """At most embed.max_per_frame crops this frame, tracks never embedded first, then the longest waiting; the
        rest wait for a later frame, so analysis never waits on a queue of crops."""
        due, self._due = sorted(self._due, key=lambda d: d[:3]), []
        for _, _, _, t, m, obs in due[:max(0, self.t.embed.max_per_frame)]:
            # what it looks like now, so actions like eating show up, not its sharpest moment
            m.embedding = self.embedder.image(crop(image, t.box))
            m.embed_ts, m.embed_signature = ts, signature(image, t.box, self.t.appearance)
            m.embeds += 1
            self.counts["embeds"] += 1
            obs.embedding = m.embedding

    def keep_people(self, person_ids: set[str]) -> None:
        """Forgets every face look and name of someone no longer enrolled, so they are not named again."""
        for m in self.memo.values():
            m.looks = [x for x in m.looks if x.person_id in person_ids]
            if m.face is not None and m.face.person_id not in person_ids:
                m.face = None

    def _wants_pose(self, t: Track, ts: float, m: _TrackMemo) -> bool:
        if self.needs.pose:
            return True
        if not self.needs.posture:
            return False
        c = self.t.pose
        x, y, w, h = t.box
        if m.last_box is not None and ts > m.last_ts:
            drop = ((y + h / 2) - (m.last_box[1] + m.last_box[3] / 2)) / max(m.last_box[3], 1.0) / (ts - m.last_ts)
        else:
            drop = 0.0
        m.last_box, m.last_ts = t.box, ts
        if w / max(h, 1.0) >= c.suspicious_aspect or drop >= c.suspicious_drop:
            m.suspicious_until = ts + c.suspicious_hold_s
        return ts <= m.suspicious_until

    def _keyframe(self, image: np.ndarray, t: Track, ts: float, m: _TrackMemo) -> None:
        c = crop(image, t.box)
        if c.size == 0:
            return
        m.keyframe_ts = ts
        key = society_key(self.camera.workspace_id, self.camera.id, "kf", f"{t.id}_{int(ts * 1000)}.jpg")
        ok, jpg = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, self.t.perceive.jpeg_quality])
        if ok:
            self.blobs.put(key, jpg.tobytes(), "image/jpeg")
            m.keyframe_key = key
        if t.cls == "person":
            m.signature = signature(image, t.box, self.t.appearance)
        score = sharpness(c) * c.shape[0] * c.shape[1]
        if score > m.best_score:
            m.best_score, m.best_key, m.best_crop = score, m.keyframe_key, c.copy()
