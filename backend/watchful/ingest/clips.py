import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from watchful.ingest.sources import Frame
from watchful.store.blobs import BlobStore
from watchful.tuning import ClipTuning


def encode_mp4(frames: list[np.ndarray], fps: float, cfg: ClipTuning = ClipTuning()) -> bytes:
    h, w = frames[0].shape[:2]
    w2, h2 = w - w % 2, h - h % 2
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w2}x{h2}",
               "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in frames:
            if f.shape[:2] != (h2, w2):
                f = cv2.resize(f, (w2, h2))
            proc.stdin.write(np.ascontiguousarray(f).tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to write the clip")
        return out.read_bytes()


class ClipRecorder:
    """Keeps recent frames; a request writes the seconds before and after a moment as H.264."""

    def __init__(self, blobs: BlobStore, fps: float, cfg: ClipTuning = ClipTuning()):
        self.blobs, self.fps, self.cfg = blobs, fps, cfg
        self.buffer: deque[tuple[float, bytes]] = deque()  # JPEGs: a few MB instead of hundreds of raw frames
        self.requests: list[tuple[float, str]] = []
        self.threads: list[threading.Thread] = []

    def on_frame(self, frame: Frame) -> None:
        ok, jpg = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            self.buffer.append((frame.ts, jpg.tobytes()))
        keep = self.cfg.before_s + self.cfg.after_s + 2
        while self.buffer and self.buffer[0][0] < frame.ts - keep:
            self.buffer.popleft()
        due = [r for r in self.requests if frame.ts >= r[0] + self.cfg.after_s]
        for r in due:
            self.requests.remove(r)
            self._write(*r)

    def request(self, ts: float, key: str) -> None:
        self.requests.append((ts, key))

    def _write(self, ts: float, key: str) -> None:
        frames = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for t, j in self.buffer
                  if ts - self.cfg.before_s <= t <= ts + self.cfg.after_s]
        if not frames:
            return
        t = threading.Thread(target=lambda: self.blobs.put(key, encode_mp4(frames, self.fps, self.cfg), "video/mp4"),
                             daemon=True)
        t.start()
        self.threads.append(t)

    def close(self) -> None:
        for r in list(self.requests):
            self._write(*r)
        self.requests.clear()
        for t in self.threads:
            t.join()
