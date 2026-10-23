import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

Status = Literal["live", "reconnecting", "ended"]
BACKOFF_S = (1, 2, 4, 8, 16, 30)


@dataclass
class Frame:
    image: np.ndarray
    ts: float
    index: int
    pos: float | None = None  # position in the source file, for files and demo loops


class FrameSource(Protocol):
    status: Status

    def read(self, timeout: float = 1.0) -> Frame | None: ...

    def close(self) -> None: ...


def fit_width(image: np.ndarray, width: int) -> np.ndarray:
    h, w = image.shape[:2]
    if w <= width:
        return image
    return cv2.resize(image, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)


class FileSource:
    """Samples a video file at the analysis rate; ts is start_epoch plus the video clock."""

    def __init__(self, path: str | Path, fps: float, loop: bool = False, realtime: bool = False,
                 width: int = 960, start_epoch: float | None = None):
        self.path = str(path)
        self.step = 1.0 / fps
        self.loop = loop
        self.realtime = realtime
        self.width = width
        self.start_epoch = time.time() if start_epoch is None else start_epoch
        self.status: Status = "live"
        self._open()
        self._offset = 0.0
        self._next_t = 0.0
        self._index = 0
        self._wall0 = time.monotonic()

    def _open(self) -> None:
        self.cap = cv2.VideoCapture(self.path, cv2.CAP_FFMPEG)
        self.src_fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._pos = 0

    def read(self, timeout: float = 1.0) -> Frame | None:
        if self.status == "ended":
            return None
        while True:
            if not self.cap.grab():
                if not self.loop or self._pos == 0:
                    self.status = "ended"
                    self.cap.release()
                    return None
                length = self._pos / self.src_fps
                self._offset += length
                self._next_t -= length
                self.cap.release()
                self._open()
                continue
            t = self._pos / self.src_fps
            self._pos += 1
            if t + 1e-6 < self._next_t:
                continue
            ok, image = self.cap.retrieve()
            if not ok:
                continue
            self._next_t = max(self._next_t + self.step, t)
            clock = self._offset + t
            if self.realtime:
                wait = clock - (time.monotonic() - self._wall0)
                if wait > 0:
                    time.sleep(wait)
            self._index += 1
            return Frame(fit_width(image, self.width), self.start_epoch + clock, self._index, t)

    def close(self) -> None:
        self.cap.release()
        self.status = "ended"


class _Latest:
    def __init__(self):
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._last_returned = -1

    def put(self, frame: Frame) -> None:
        with self._cond:
            self._frame = frame
            self._cond.notify_all()

    def take(self, timeout: float) -> Frame | None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                f = self._frame
                if f is not None and f.index != self._last_returned:
                    self._last_returned = f.index
                    return f
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)


class StreamSource:
    """RTSP or HTTP camera read on a background thread; read() returns the newest frame."""

    def __init__(self, url: str, fps: float, width: int = 960):
        self.url = url
        self.step = 1.0 / fps
        self.width = width
        self.status: Status = "reconnecting"
        self._latest = _Latest()
        self._stop = threading.Event()
        self._last_read = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        attempt = 0
        index = 0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
            got_frame = False
            while not self._stop.is_set() and cap.isOpened():
                ok, image = cap.read()
                if not ok:
                    break
                got_frame = True
                self.status = "live"
                attempt = 0
                index += 1
                self._latest.put(Frame(image, time.time(), index))
            cap.release()
            if self._stop.is_set():
                break
            self.status = "reconnecting"
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            attempt = 0 if got_frame else attempt + 1
            self._stop.wait(delay)

    def read(self, timeout: float = 1.0) -> Frame | None:
        wait = self._last_read + self.step - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, timeout))
        frame = self._latest.take(timeout)
        if frame is None:
            return None
        self._last_read = time.monotonic()
        return Frame(fit_width(frame.image, self.width), frame.ts, frame.index)

    def close(self) -> None:
        self._stop.set()
        self.status = "ended"


class PushSource:
    """Webcam frames pushed from the browser as JPEG; keeps only the newest."""

    def __init__(self, fps: float, width: int = 960):
        self.step = 1.0 / fps
        self.width = width
        self.status: Status = "live"
        self._latest = _Latest()
        self._index = 0

    def push(self, jpeg: bytes, ts: float) -> None:
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return
        self._index += 1
        self._latest.put(Frame(fit_width(image, self.width), ts, self._index))

    def read(self, timeout: float = 1.0) -> Frame | None:
        return self._latest.take(timeout)

    def close(self) -> None:
        self.status = "ended"
