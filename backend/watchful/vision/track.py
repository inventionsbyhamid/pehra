from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.vision.detect import Detection

Box = tuple[float, float, float, float]
HIGH_SCORE = 0.5


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
    history: list[tuple[float, Box]] = field(default_factory=list)
    started: bool = False
    kf: cv2.KalmanFilter | None = field(default=None, repr=False)
    _pred_ts: float = 0.0

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


class Tracker:
    """Kalman plus IoU tracking by detection, two passes like ByteTrack."""

    def __init__(self, iou_threshold: float = 0.3, max_age_s: float = 2.0, min_hits: int = 2):
        self.iou_threshold = iou_threshold
        self.max_age_s = max_age_s
        self.min_hits = min_hits
        self.tracks: list[Track] = []
        self._next_id = 1

    def update(self, dets: list[Detection], ts: float) -> TrackUpdate:
        preds = {t.id: t.predict(ts) for t in self.tracks}
        high = [d for d in dets if d.score >= HIGH_SCORE]
        low = [d for d in dets if d.score < HIGH_SCORE]
        matches, rest, new_dets = _greedy(self.tracks, preds, high, self.iou_threshold)
        low_matches, rest, _ = _greedy(rest, preds, low, self.iou_threshold)

        started = []
        for t, d in matches + low_matches:
            t.correct(d, ts)
            if not t.started and t.hits >= self.min_hits:
                t.started = True
                started.append(t)
        for t in rest:
            t.box = preds[t.id]

        for d in new_dets:
            t = Track(self._next_id, d.cls, d.box, ts, ts, score=d.score, kf=_kalman(d.box),
                      history=[(ts, d.box)], _pred_ts=ts)
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
