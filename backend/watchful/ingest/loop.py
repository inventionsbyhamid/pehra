"""Demo cameras share their decoding: one looping FileSource per clip in a camera process, read in real time on a
thread of its own, hands its newest frame to every demo camera on that clip (LoopTap), so twenty demo sandboxes
mean one decoder per clip per camera process, not one per camera. The decoder rests while no camera has read for
IDLE_S (a demo camera nobody watches is paused, pool.CameraRunner._paused).

Each tap gets its own copy of the picture (a camera's pipeline may draw on or blur its frame) stamped with the wall
clock, as a live camera's would be, so an incident on a loop that rested an hour is not an hour old."""
import logging
import threading
import time

from watchful.ingest.sources import FileSource, Frame, Status, _Latest

log = logging.getLogger(__name__)

IDLE_S = 2.0  # no tap has read for this long: the decoder rests (a reading camera asks every frame)


class SharedLoop:
    """One clip decoded once, in real time and looping, for every tap reading it."""

    def __init__(self, path: str, fps: float, width: int):
        self.key = (path, fps, width)
        self.path, self.fps, self.width = path, fps, width
        self.taps: set["LoopTap"] = set()
        self.status: Status = "live"
        self.frames = 0  # decoded frames handed out, for tests and measurements
        self._wanted = threading.Event()
        self._stop = threading.Event()
        self._last_read = time.monotonic()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"loop-{self.path.rsplit('/', 1)[-1]}", daemon=True)
        self._thread.start()

    def wanted(self) -> None:
        self._last_read = time.monotonic()
        self._wanted.set()

    def _run(self) -> None:
        source = None
        try:
            source = FileSource(self.path, self.fps, loop=True, realtime=True, width=self.width)
            while not self._stop.is_set():
                if time.monotonic() - self._last_read > IDLE_S:
                    self._wanted.clear()
                    self._wanted.wait(0.5)
                    continue
                frame = source.read(timeout=1.0)
                if frame is None:
                    self.status = source.status
                    if source.status == "ended":
                        break
                    continue
                self.frames += 1
                for tap in list(self.taps):
                    tap._latest.put(frame)
        except Exception:
            log.exception("the demo clip %s stopped", self.path)
        finally:
            if source is not None:
                source.close()
            self.status = "ended"
            for tap in list(self.taps):
                tap._ended.set()

    def stop(self) -> None:
        self._stop.set()
        self._wanted.set()


class LoopTap:
    """A demo camera's source: the shared loop's newest frame, its own copy, at most `fps` a second."""

    def __init__(self, loop: SharedLoop, registry: "Loops"):
        self.loop, self.registry = loop, registry
        self.step = 1.0 / loop.fps
        self._latest = _Latest()
        self._ended = threading.Event()
        self._index = 0
        self._last_read = 0.0

    @property
    def status(self) -> Status:
        return "ended" if self._ended.is_set() else self.loop.status

    def read(self, timeout: float = 1.0) -> Frame | None:
        if self._ended.is_set():
            return None
        self.loop.wanted()
        frame = self._latest.take(timeout)
        if frame is None:
            return None
        self._index += 1
        return Frame(frame.image.copy(), time.time(), self._index, frame.pos)

    def close(self) -> None:
        self._ended.set()
        self.registry.release(self)


class Loops:
    """This process's shared loops, one per clip, rate and width; a loop stops when its last tap closes."""

    def __init__(self):
        self.loops: dict[tuple, SharedLoop] = {}
        self._lock = threading.Lock()

    def tap(self, path: str, fps: float, width: int) -> LoopTap:
        key = (str(path), fps, width)
        with self._lock:
            loop = self.loops.get(key)
            if loop is None or loop.status == "ended":
                loop = self.loops[key] = SharedLoop(str(path), fps, width)
                loop.start()
            tap = LoopTap(loop, self)
            loop.taps.add(tap)
            return tap

    def release(self, tap: LoopTap) -> None:
        with self._lock:
            loop = tap.loop
            loop.taps.discard(tap)
            if not loop.taps:
                loop.stop()
                if self.loops.get(loop.key) is loop:
                    del self.loops[loop.key]

    def decoders(self) -> int:
        with self._lock:
            return len(self.loops)


LOOPS = Loops()


def demo_source(path: str, fps: float, width: int, realtime: bool = True):
    """A demo camera's frames: the clip's shared real time loop; a private FileSource when the clip is read faster
    than real time (tests and tools, where each camera must see every frame)."""
    if not realtime:
        return FileSource(path, fps, loop=True, realtime=False, width=width)
    return LOOPS.tap(path, fps, width)
