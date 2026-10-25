"""What the verifier sees: frames around the moment plus the track's path and poses, packed in the worker
so the AI check needs nothing else from it."""
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from watchful.agent.tools import ToolContext
from watchful.ingest.sources import Frame
from watchful.rules.engine import Candidate
from watchful.store.blobs import SocietyBlobs
from watchful.tuning import EvidenceTuning
from watchful.vision.reason import Tick

Box = tuple[float, float, float, float]
ZONE_COLOR = (0, 215, 255)
BOX_COLOR = (40, 40, 230)


@dataclass
class EvidencePack:
    candidate: Candidate
    frames: list[tuple[float, bytes]]
    boxes: list[tuple[float, Box]]  # in the frames' pixels
    poses: list[dict] = field(default_factory=list)
    zones: list[tuple[str, list]] = field(default_factory=list)  # name and normalized polygon
    workspace_id: str | None = None  # the camera's society; the AI check reads only its pictures and rows


def _jpeg(image: np.ndarray, width: int) -> tuple[bytes, float]:
    h, w = image.shape[:2]
    scale = min(1.0, width / w)
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes(), scale


class EvidenceBuffer:
    def __init__(self, cfg: EvidenceTuning = EvidenceTuning(), zones: list | None = None,
                 workspace_id: str | None = None):
        self.cfg, self.workspace_id = cfg, workspace_id
        self.zones = [(z.name, z.polygon) for z in zones or []]
        self.frames: deque[tuple[float, bytes]] = deque()
        self.paths: dict[int, deque] = {}
        self.poses: dict[int, deque] = {}
        self.scale = 1.0
        self._last = float("-inf")

    def add(self, frame: Frame, tick: Tick) -> None:
        keep = self.cfg.before_s + self.cfg.after_s + 5
        if frame.ts - self._last >= 1 / self.cfg.fps:
            jpg, self.scale = _jpeg(frame.image, self.cfg.width)
            self.frames.append((frame.ts, jpg))
            self._last = frame.ts
        while self.frames and self.frames[0][0] < frame.ts - keep:
            self.frames.popleft()
        for s in tick.tracks:
            self.paths.setdefault(s.track_id, deque(maxlen=300)).append((frame.ts, s.box))
            if s.fresh_pose and s.pose is not None:
                self.poses.setdefault(s.track_id, deque(maxlen=60)).append(
                    {"ts": frame.ts, "torso_angle": round(s.pose.torso_angle_deg, 1)})
        stale = [tid for tid, p in self.paths.items() if p and p[-1][0] < frame.ts - 60]
        for tid in stale:
            self.paths.pop(tid, None)
            self.poses.pop(tid, None)

    def pack(self, c: Candidate) -> EvidencePack:
        frames = [(ts, j) for ts, j in self.frames if c.ts - self.cfg.before_s <= ts <= c.ts + self.cfg.after_s]
        k = self.scale
        boxes = [(ts, (b[0] * k, b[1] * k, b[2] * k, b[3] * k)) for ts, b in self.paths.get(c.track_id, [])]
        return EvidencePack(c, frames, boxes, list(self.poses.get(c.track_id, [])), self.zones, self.workspace_id)


def file_pack(path: str, start_epoch: float, c: Candidate, path_boxes: list[tuple[float, Box]],
              poses: list[dict], cfg: EvidenceTuning = EvidenceTuning(), zones: list | None = None,
              workspace_id: str | None = None) -> EvidencePack:
    """Evidence for a candidate found in a recording, read back from the file."""
    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    frames, scale = [], 1.0
    t = max(0.0, c.ts - start_epoch - cfg.before_s)
    end = c.ts - start_epoch + cfg.after_s
    while t <= end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            break
        jpg, scale = _jpeg(img, cfg.width)
        frames.append((start_epoch + t, jpg))
        t += 1 / cfg.fps
    cap.release()
    return EvidencePack(c, frames, [(ts, tuple(v * scale for v in b)) for ts, b in path_boxes], poses,
                        [(z.name, z.polygon) for z in zones or []], workspace_id)


def _nearest(items: list, ts: float):
    return min(items, key=lambda it: abs(it[0] - ts)) if items else None


def overlay(jpeg: bytes, zones: list[tuple[str, list]], box: Box | None) -> bytes:
    """Draws the zones and the flagged object's box so the AI sees what OpenCV measured against."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    for name, poly in zones:
        pts = np.array([[x * w, y * h] for x, y in poly], np.int32)
        cv2.polylines(img, [pts], True, ZONE_COLOR, 2, cv2.LINE_AA)
        cv2.putText(img, name, tuple(int(v) for v in pts.min(0) + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ZONE_COLOR, 1,
                    cv2.LINE_AA)
    if box is not None:
        x, y, bw, bh = (int(v) for v in box)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), BOX_COLOR, 2)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def evidence_context(pack: EvidencePack, repo, blobs) -> ToolContext:
    """The AI check's tools for one candidate, bound to the camera's society: its repository and its pictures."""
    if pack.workspace_id and pack.workspace_id != repo.workspace_id:
        raise ValueError("an evidence pack is checked with its own society's repository")
    if pack.workspace_id:
        blobs = SocietyBlobs(blobs, pack.workspace_id)
    frames = pack.frames

    def drawn(ts: float, jpeg: bytes) -> bytes:
        b = _nearest(pack.boxes, ts)
        return overlay(jpeg, pack.zones, b[1] if b and abs(b[0] - ts) <= 1.0 else None)

    def get_frames(t0: float, t1: float, n: int) -> list[bytes]:
        inside = [(ts, j) for ts, j in frames if t0 <= ts <= t1]
        if not inside:
            near = _nearest(frames, (t0 + t1) / 2)
            return [drawn(*near)] if near else []
        step = max(1, len(inside) // max(n, 1))
        return [drawn(ts, j) for ts, j in inside[::step][:n]]

    def crops(track_id: int, ts: float) -> bytes | None:
        f, b = _nearest(frames, ts), _nearest(pack.boxes, ts)
        if not f or not b:
            return None
        img = cv2.imdecode(np.frombuffer(f[1], np.uint8), cv2.IMREAD_COLOR)
        x, y, w, h = (int(v) for v in b[1])
        pad = int(0.15 * h)
        crop = img[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        return cv2.imencode(".jpg", crop)[1].tobytes() if crop.size else None

    def poses(track_id: int, seconds: float) -> list[dict]:
        return [p for p in pack.poses if p["ts"] >= pack.candidate.ts - seconds]

    return ToolContext(repo, blobs, pack.candidate.camera_id, get_frames, crops, poses, lambda tid: pack.boxes,
                       moment=pack.candidate.ts, track_id=pack.candidate.track_id)
