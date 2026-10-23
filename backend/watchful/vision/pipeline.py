from dataclasses import dataclass

import cv2
import numpy as np

from watchful.config import Settings
from watchful.ingest.sources import Frame
from watchful.store.blobs import BlobStore
from watchful.store.db import Camera, Repository
from watchful.tuning import Tuning
from watchful.vision.faces import FaceIndex
from watchful.vision.perceive import Needs, Perceiver
from watchful.vision.reason import Reasoner, Tick

ZONE_COLOR = (80, 200, 255)
TRACK_COLOR = (90, 220, 120)
ALERT_COLOR = (60, 60, 230)


@dataclass
class FrameResult:
    tick: Tick
    moving: bool
    annotated_jpeg: bytes | None


def annotate(image: np.ndarray, camera: Camera, tick: Tick) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    for z in camera.zones:
        pts = z.contour((w, h)).astype(np.int32)
        cv2.polylines(out, [pts], True, ZONE_COLOR, 2)
        cv2.putText(out, z.name, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ZONE_COLOR, 1, cv2.LINE_AA)
    for s in tick.tracks:
        x, y, bw, bh = (int(v) for v in s.box)
        color = ALERT_COLOR if s.posture != "upright" else TRACK_COLOR
        cv2.rectangle(out, (x, y), (x + bw, y + bh), color, 2)
        label = s.known_name or f"{s.cls} {s.track_id}"
        if s.posture != "upright":
            label += f" {s.posture}"
        cv2.putText(out, label, (x, max(12, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


class CameraPipeline:
    """Perceiver then Reasoner for a live source; events go to the repository."""

    def __init__(self, camera: Camera, repo: Repository, blobs: BlobStore, faces: FaceIndex | None,
                 settings: Settings, tuning: Tuning, needs: Needs, embedder=None):
        self.camera, self.repo = camera, repo
        self.perceiver = Perceiver(camera, settings, tuning, blobs, faces, needs, embedder)
        self.reasoner = Reasoner(camera, tuning, postures=needs.pose or needs.posture)

    def set_needs(self, needs: Needs) -> None:
        self.perceiver.set_needs(needs)
        self.reasoner.postures = needs.pose or needs.posture

    def stats(self) -> dict:
        return self.perceiver.stats()

    def finish(self, ts: float, pos: float | None = None) -> Tick:
        """Closes open tracks at the end of a file."""
        tick = self.reasoner.step(self.perceiver.finish(ts, pos))
        if tick.events:
            self.repo.add_events(tick.events)
        return tick

    def process(self, frame: Frame, draw: bool = False) -> FrameResult:
        p = self.perceiver.perceive(frame)
        tick = self.reasoner.step(p)
        if tick.events:
            self.repo.add_events(tick.events)
        jpeg = None
        if draw:
            ok, buf = cv2.imencode(".jpg", annotate(frame.image, self.camera, tick), [cv2.IMWRITE_JPEG_QUALITY, 75])
            jpeg = buf.tobytes() if ok else None
        return FrameResult(tick, p.moving, jpeg)
