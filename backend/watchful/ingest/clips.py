import logging
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from concurrent.futures import Executor
from pathlib import Path

import cv2
import numpy as np

from watchful.ingest.sources import Frame
from watchful.store.blobs import BlobStore, in_society
from watchful.tuning import ClipTuning

log = logging.getLogger("watchful.clips")


def encode_mp4(frames, fps: float, cfg: ClipTuning = ClipTuning()) -> bytes:
    """H.264 from frames, or from JPEG bytes decoded one at a time, so a clip waiting for the CPU holds only its
    JPEGs (a few MB), not every raw frame."""
    frames = iter(frames)
    first = next(frames)
    decode = isinstance(first, bytes)
    image = cv2.imdecode(np.frombuffer(first, np.uint8), cv2.IMREAD_COLOR) if decode else first
    h, w = image.shape[:2]
    w2, h2 = w - w % 2, h - h % 2
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w2}x{h2}",
               "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
               "-threads", str(cfg.threads), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        if cfg.nice and shutil.which("nice"):
            cmd = ["nice", "-n", str(cfg.nice), *cmd]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        while image is not None:
            if image.shape[:2] != (h2, w2):
                image = cv2.resize(image, (w2, h2))
            proc.stdin.write(np.ascontiguousarray(image).tobytes())
            item = next(frames, None)
            image = None if item is None else (cv2.imdecode(np.frombuffer(item, np.uint8), cv2.IMREAD_COLOR)
                                               if decode else item)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to write the clip")
        return out.read_bytes()


class ClipRecorder:
    """Keeps recent frames as JPEGs; a request writes the seconds before and after a moment as H.264. Frames arrive
    on the camera's reader thread and requests from the analysis loop, so both go under a lock. Clips are written on
    `writer` (an executor a camera process shares between its cameras, so at most a few encode at once), else on a
    thread each. With `workspace_id`, a key outside that society's prefix is refused."""

    def __init__(self, blobs: BlobStore, fps: float, cfg: ClipTuning = ClipTuning(), workspace_id: str | None = None,
                 writer: Executor | None = None):
        self.blobs, self.fps, self.cfg, self.workspace_id, self.writer = blobs, fps, cfg, workspace_id, writer
        self.buffer: deque[tuple[float, bytes]] = deque()  # JPEGs: a few MB instead of hundreds of raw frames
        self.requests: list[tuple[float, str]] = []
        self.pending: list = []  # threads or futures of clips being written
        self._lock = threading.Lock()

    def on_frame(self, frame: Frame) -> None:
        ok, jpg = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        keep = self.cfg.before_s + self.cfg.after_s + 2
        with self._lock:
            if ok:
                self.buffer.append((frame.ts, jpg.tobytes()))
            while self.buffer and self.buffer[0][0] < frame.ts - keep:
                self.buffer.popleft()
            due = [r for r in self.requests if frame.ts >= r[0] + self.cfg.after_s]
            for r in due:
                self.requests.remove(r)
                self._write(*r)

    def request(self, ts: float, key: str) -> None:
        if self.workspace_id and not in_society(key, self.workspace_id):
            raise ValueError(f"clip key {key} is outside its society")
        with self._lock:
            self.requests.append((ts, key))

    def _write(self, ts: float, key: str) -> None:
        jpegs = [j for t, j in self.buffer if ts - self.cfg.before_s <= t <= ts + self.cfg.after_s]
        if not jpegs:
            return

        def write() -> None:
            try:
                self.blobs.put(key, encode_mp4(jpegs, self.fps, self.cfg), "video/mp4")
            except Exception:
                log.exception("writing the clip %s failed", key)

        if self.writer is not None:
            job = self.writer.submit(write)
        else:
            job = threading.Thread(target=write, daemon=True)
            job.start()
        self.pending = [j for j in self.pending if not _done(j)] + [job]

    def close(self) -> None:
        with self._lock:
            for r in list(self.requests):
                self._write(*r)
            self.requests.clear()
            pending = list(self.pending)
        for job in pending:
            job.join() if isinstance(job, threading.Thread) else job.result()


def _done(job) -> bool:
    return not job.is_alive() if isinstance(job, threading.Thread) else job.done()
