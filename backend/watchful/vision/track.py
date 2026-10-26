import time
from collections import Counter, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.tuning import TrackTuning
from watchful.vision.appearance import same_person, signature
from watchful.vision.detect import Detection

Box = tuple[float, float, float, float]


def session_id_base() -> int:
    """Track ids start from the clock so ids from different runs of a camera never collide."""
    return (int(time.time()) % 10**7) * 10**6


def iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _kalman(box: Box) -> cv2.KalmanFilter:
    kf = cv2.KalmanFilter(8, 4)
    kf.measurementMatrix = np.eye(4, 8, dtype=np.float32)
    kf.processNoiseCov = np.diag([1, 1, 1, 1, 10, 10, 1, 1]).astype(np.float32)
    kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 4
    kf.errorCovPost = np.diag([10, 10, 10, 10, 1000, 1000, 100, 100]).astype(np.float32)
    x, y, w, h = box
    kf.statePost = np.array([[x + w / 2], [y + h / 2], [w], [h], [0], [0], [0], [0]], np.float32)
    return kf


@dataclass
class Track:
    id: int
    cls: str
    box: Box
    first_ts: float
    last_ts: float
    hits: int = 1
    score: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=150))  # recent (ts, box) only
    started: bool = False
    kf: cv2.KalmanFilter | None = field(default=None, repr=False)
    _pred_ts: float = 0.0
    signature: np.ndarray | None = field(default=None, repr=False)  # clothing, for the far pass's ties
    signature_ts: float = float("-inf")

    def predict(self, ts: float) -> Box:
        dt = max(ts - self._pred_ts, 1e-3)
        f = np.eye(8, dtype=np.float32)
        for i in range(4):
            f[i, i + 4] = dt
        self.kf.transitionMatrix = f
        s = self.kf.predict().flatten()
        self._pred_ts = ts
        w, h = max(float(s[2]), 1.0), max(float(s[3]), 1.0)
        return (float(s[0]) - w / 2, float(s[1]) - h / 2, w, h)

    def correct(self, det: Detection, ts: float) -> None:
        x, y, w, h = det.box
        self.kf.correct(np.array([[x + w / 2], [y + h / 2], [w], [h]], np.float32))
        self.box = det.box
        self.score = det.score
        self.last_ts = ts
        self.hits += 1
        self.history.append((ts, det.box))


@dataclass
class TrackUpdate:
    active: list[Track]
    started: list[Track]
    ended: list[Track]


def _greedy(tracks: list[Track], preds: dict[int, Box], dets: list[Detection], threshold: float):
    pairs = []
    for ti, t in enumerate(tracks):
        for di, d in enumerate(dets):
            if d.cls == t.cls:
                v = iou(preds[t.id], d.box)
                if v >= threshold:
                    pairs.append((v, ti, di))
    pairs.sort(reverse=True)
    used_t, used_d, matches = set(), set(), []
    for _, ti, di in pairs:
        if ti not in used_t and di not in used_d:
            used_t.add(ti)
            used_d.add(di)
            matches.append((tracks[ti], dets[di]))
    rest_t = [t for i, t in enumerate(tracks) if i not in used_t]
    rest_d = [d for i, d in enumerate(dets) if i not in used_d]
    return matches, rest_t, rest_d


def _centre(box: Box) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


class Tracker:
    """Kalman plus IoU tracking by detection, two passes like ByteTrack, then a third for what they leave: at 1 to 2
    analysed frames a second a walker moves past its own width between frames, so IoU alone would restart its id
    (another enter, a lost line crossing). The third pass joins a track and a detection of the same class and size
    within a distance that grows with the time since the track was seen; clothes break ties when the frame is given."""

    def __init__(self, cfg: TrackTuning = TrackTuning(), first_id: int | None = None):
        self.cfg = cfg
        self.iou_threshold = cfg.iou
        self.max_age_s = cfg.max_age_s
        self.min_hits = cfg.min_hits
        self.tracks: list[Track] = []
        self._next_id = first_id if first_id is not None else session_id_base() + 1

    def update(self, dets: list[Detection], ts: float, image: np.ndarray | None = None) -> TrackUpdate:
        preds = {t.id: t.predict(ts) for t in self.tracks}
        high = [d for d in dets if d.score >= self.cfg.high_score]
        low = [d for d in dets if d.score < self.cfg.high_score]
        matches, rest, new_dets = _greedy(self.tracks, preds, high, self.iou_threshold)
        low_matches, rest, _ = _greedy(rest, preds, low, self.iou_threshold)
        far, rest, new_dets = self._far(rest, preds, new_dets, ts, image)

        started = []
        for t, d in matches + low_matches + far:
            t.correct(d, ts)
            self._sign(t, image, ts)
            if not t.started and t.hits >= self.min_hits:
                t.started = True
                started.append(t)
        for t in rest:
            t.box = preds[t.id]

        for d in new_dets:
            t = Track(self._next_id, d.cls, d.box, ts, ts, score=d.score, kf=_kalman(d.box),
                      history=deque([(ts, d.box)], maxlen=150), _pred_ts=ts)
            self._sign(t, image, ts)
            self._next_id += 1
            self.tracks.append(t)
            if self.min_hits <= 1:
                t.started = True
                started.append(t)

        ended, alive = [], []
        for t in self.tracks:
            if ts - t.last_ts > self.max_age_s:
                if t.started:
                    ended.append(t)
            else:
                alive.append(t)
        self.tracks = alive
        active = [t for t in alive if t.started]
        return TrackUpdate(active, started, ended)

    def _far(self, tracks: list[Track], preds: dict[int, Box], dets: list[Detection], ts: float,
             image: np.ndarray | None):
        """The third pass: (matches, tracks left, detections left)."""
        c = self.cfg
        pairs = []
        for ti, t in enumerate(tracks):
            h = max(t.box[3], 1.0)
            gate = c.far_base + c.far_speed_by_class.get(t.cls, c.far_speed) * max(ts - t.last_ts, 0.0)
            if gate <= 0:
                continue
            (px, py) = _centre(preds[t.id])
            for di, d in enumerate(dets):
                if d.cls != t.cls or max(d.box[3], h) > c.far_size_ratio * max(min(d.box[3], h), 1.0):
                    continue
                dx, dy = _centre(d.box)
                share = ((px - dx) ** 2 + (py - dy) ** 2) ** 0.5 / h / gate
                if share <= 1.0:
                    pairs.append([share, ti, di])
        if not pairs:
            return [], tracks, dets
        if image is not None and c.far_appearance > 0:
            per_t, per_d = Counter(p[1] for p in pairs), Counter(p[2] for p in pairs)
            sigs: dict[int, np.ndarray] = {}
            for p in pairs:
                t = tracks[p[1]]
                if (per_t[p[1]] > 1 or per_d[p[2]] > 1) and t.signature is not None:
                    if p[2] not in sigs:
                        sigs[p[2]] = signature(image, dets[p[2]].box)
                    p[0] += c.far_appearance * same_person(t.signature, sigs[p[2]])
        pairs.sort()
        used_t, used_d, matches = set(), set(), []
        for _, ti, di in pairs:
            if ti not in used_t and di not in used_d:
                used_t.add(ti)
                used_d.add(di)
                matches.append((tracks[ti], dets[di]))
        return (matches, [t for i, t in enumerate(tracks) if i not in used_t],
                [d for i, d in enumerate(dets) if i not in used_d])

    def _sign(self, t: Track, image: np.ndarray | None, ts: float) -> None:
        if image is not None and self.cfg.far_appearance > 0 and ts - t.signature_ts >= self.cfg.signature_every_s:
            t.signature, t.signature_ts = signature(image, t.box), ts
