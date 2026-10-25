#!/usr/bin/env python3
"""The camera bridge: runs on any computer at the society, reads each camera's RTSP stream on the local network with
OpenCV and sends JPEG frames to the control room over a secure WebSocket, one device token per camera. Nothing
listens here, so no port is opened on the society's router.

    python pehra_bridge.py --server https://control.example.com --cameras-file cameras.txt
    python pehra_bridge.py --server https://control.example.com --camera TOKEN=rtsp://user:pass@192.168.1.64/...
    python pehra_bridge.py --server https://control.example.com --token TOKEN --rtsp rtsp://... [--fps N]

The server says how many frames a second to send and how wide (--fps can only lower the rate). RTSP is read over
TCP. A lost camera or a lost connection is retried with growing waits. A camera whose link the control room no
longer knows stops; when every camera has stopped the bridge stays running and idle, saying what to do, rather than
exiting (a service manager restarting it would retry the old links over and over). Passwords never reach the log:
cameras are shown by scheme, host and port, and tokens by their first four characters."""
import argparse
import json
import logging
import logging.handlers
import os
import random
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # read before any capture opens
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")  # FFmpeg quiet: its messages could carry an address
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

log = logging.getLogger("bridge")

BACKOFF_S = (1, 2, 4, 8, 16, 30)
OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 8000
FRESH_S = 2.0  # a frame older than this is not sent
HELLO_TIMEOUT_S = 10.0
DEFAULTS = {"fps": 5.0, "width": 960, "quality": 0.72}  # until the server's hello says otherwise
STOP_CODES = {4400: "this camera is not set up to be sent by the bridge",
              4404: "the server does not know this camera's token",
              4410: "this camera's link was replaced or removed"}
WAIT_CODES = {4409: "another device is sending this camera", 4412: "the camera is turned off in the control room",
              4429: "too many tries from this address"}
WAIT_S = 60
IDLE_REMIND_S = 3600  # how often an idle bridge repeats what to do
LOG_BYTES, LOG_FILES = 5_000_000, 3
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CREDENTIALS = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]*@")


def redact(text: str) -> str:
    """Any address in the text without its user and password."""
    return CREDENTIALS.sub(r"\1", str(text))


def where(url: str) -> str:
    """How the log names a camera: scheme, host and port only. Some recorders take the user and password in the
    path or query (/user=admin_password=..., ?password=...), so neither is ever shown."""
    try:
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "an unreadable address"
    if not parts.scheme or not parts.hostname:
        return "a file on this computer"
    return f"{parts.scheme}://{parts.hostname}{port}"


class Backoff:
    def __init__(self, delays=BACKOFF_S):
        self.delays, self.attempt = tuple(delays), 0

    def next(self) -> float:
        delay = self.delays[min(self.attempt, len(self.delays) - 1)]
        self.attempt += 1
        return delay * random.uniform(0.8, 1.2)

    def reset(self) -> None:
        self.attempt = 0


@dataclass(eq=False)
class Camera:
    token: str
    url: str
    name: str = ""
    frame: object = None
    frame_at: float = 0.0
    sent: int = 0
    connects: int = 0
    stopped_because: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.name = self.name or f"camera {self.token[:4]}... at {where(self.url)}"


def parse_camera(value: str) -> Camera:
    token, sep, url = value.strip().partition("=")
    token, url = token.strip(), url.strip()
    if not sep or not TOKEN.match(token) or not url:
        raise ValueError("a camera is written TOKEN=RTSP_ADDRESS, with the token from the control room")
    return Camera(token, url)


def read_cameras_file(path: str) -> list[Camera]:
    with open(path, encoding="utf-8") as f:
        return [parse_camera(line) for line in f if line.strip() and not line.lstrip().startswith("#")]


def socket_url(server: str, token: str) -> str:
    parts = urlsplit(server.strip().rstrip("/"))
    scheme = {"https": "wss", "http": "ws", "wss": "wss", "ws": "ws"}.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError("--server is the control room's address, for example https://control.example.com")
    return f"{scheme}://{parts.netloc}{parts.path}/ws/cam/{token}"


class Bridge:
    """Every camera gets a reader thread (RTSP into the newest frame) and a sender thread (the newest frame out)."""

    def __init__(self, server: str, cameras: list[Camera], fps: float | None = None, backoff=BACKOFF_S,
                 wait_s: float = WAIT_S, stop: threading.Event | None = None):
        self.server, self.cameras, self.fps_cap = server, cameras, fps
        self.backoff, self.wait_s = backoff, wait_s
        self.stop = stop or threading.Event()
        self.threads: list[threading.Thread] = []
        for cam in cameras:
            socket_url(server, cam.token)  # a bad --server fails here, before any thread starts

    def start(self) -> "Bridge":
        for cam in self.cameras:
            for target in (self._read, self._send):
                t = threading.Thread(target=target, args=(cam,), daemon=True)
                t.start()
                self.threads.append(t)
        return self

    def close(self) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout=10)

    def running(self) -> bool:
        """True while a camera is still trying (not stopped for good) and the bridge is not stopping."""
        return any(c.stopped_because is None for c in self.cameras) and not self.stop.is_set()

    def _read(self, cam: Camera) -> None:
        backoff = Backoff(self.backoff)
        local = "://" not in cam.url  # a recording on disk stands in for a camera in tests
        while not self.stop.is_set() and cam.stopped_because is None:
            cap = cv2.VideoCapture(cam.url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
                                                             cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS])
            step = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 25.0) if local else 0.0
            got = 0
            while cap.isOpened() and not self.stop.is_set() and cam.stopped_because is None:
                ok, image = cap.read()
                if not ok:
                    break
                with cam.lock:
                    cam.frame, cam.frame_at = image, time.monotonic()
                if got == 0:
                    log.info("%s: reading the camera", cam.name)
                    backoff.reset()
                got += 1
                if step:
                    self.stop.wait(step)
            cap.release()
            if self.stop.is_set() or cam.stopped_because is not None:
                break
            delay = backoff.next() if not got else self.backoff[0]
            if not got:
                log.warning("%s: can't read the camera, trying again in %.0f s", cam.name, delay)
            self.stop.wait(delay)

    def _latest(self, cam: Camera):
        with cam.lock:
            fresh = cam.frame is not None and time.monotonic() - cam.frame_at <= FRESH_S
            return cam.frame if fresh else None

    def _send(self, cam: Camera) -> None:
        backoff = Backoff(self.backoff)
        url = socket_url(self.server, cam.token)
        while not self.stop.is_set():
            wait = None
            try:
                with connect(url, open_timeout=HELLO_TIMEOUT_S, close_timeout=2, max_size=1 << 16,
                             user_agent_header="camera-bridge") as ws:
                    hello = self._hello(ws)
                    cam.connects += 1
                    backoff.reset()
                    log.info("%s: connected, sending %.0f frames a second", cam.name, hello["fps"])
                    self._pump(ws, cam, hello)
                    return  # stopped
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                if code in STOP_CODES:
                    cam.stopped_because = STOP_CODES[code]
                    log.error("%s: stopped, %s. Ask the society admin for a new camera link.", cam.name,
                              STOP_CODES[code])
                    return
                if code in WAIT_CODES:
                    wait = self.wait_s
                    log.warning("%s: %s, trying again in %.0f s", cam.name, WAIT_CODES[code], wait)
            except (OSError, TimeoutError, InvalidHandshake, InvalidURI, ValueError) as e:
                log.warning("%s: can't reach the server (%s)", cam.name, redact(f"{type(e).__name__}: {e}"))
            if self.stop.is_set():
                return
            wait = wait if wait is not None else backoff.next()
            log.info("%s: connecting again in %.0f s", cam.name, wait)
            self.stop.wait(wait)

    def _hello(self, ws) -> dict:
        hello = dict(DEFAULTS)
        try:
            msg = json.loads(ws.recv(timeout=HELLO_TIMEOUT_S))
            if isinstance(msg, dict) and msg.get("type") == "hello":
                hello.update({k: msg[k] for k in DEFAULTS if isinstance(msg.get(k), (int, float))})
        except (TimeoutError, ValueError, TypeError):
            pass
        if self.fps_cap:
            hello["fps"] = min(hello["fps"], self.fps_cap)
        return hello

    def _pump(self, ws, cam: Camera, hello: dict) -> None:
        interval = 1.0 / max(hello["fps"], 0.1)
        width, quality = int(hello["width"]), int(round(float(hello["quality"]) * 100))
        due = time.monotonic()
        while not self.stop.is_set():
            image = self._latest(cam)
            if image is not None:
                h, w = image.shape[:2]
                if w > width:
                    image = cv2.resize(image, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
                if ok:
                    ws.send(jpeg.tobytes())
                    cam.sent += 1
            due += interval
            try:
                ws.recv(timeout=max(0.0, due - time.monotonic()))  # the server only speaks to close
            except TimeoutError:
                pass
            if due < time.monotonic() - interval:
                due = time.monotonic()  # fell behind: carry on from now


def main(argv: list[str] | None = None, stop: threading.Event | None = None) -> int:
    p = argparse.ArgumentParser(description="Send this society's cameras to the control room.")
    p.add_argument("--server", required=True, help="the control room's address, for example https://control.example.com")
    p.add_argument("--camera", action="append", default=[], metavar="TOKEN=RTSP_ADDRESS",
                   help="a camera's token and its address on this network; repeat for more cameras")
    p.add_argument("--cameras-file", help="a file with one TOKEN=RTSP_ADDRESS per line (keeps passwords out of the "
                                          "process list)")
    p.add_argument("--token", help="one camera's token, with --rtsp")
    p.add_argument("--rtsp", help="one camera's address, with --token")
    p.add_argument("--fps", type=float, help="send at most this many frames a second (the server sets the rate)")
    p.add_argument("--log-file", help="write messages to this file as well as the screen (for Task Scheduler)")
    args = p.parse_args(argv)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.handlers.RotatingFileHandler(args.log_file, maxBytes=LOG_BYTES, backupCount=LOG_FILES,
                                                             encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        return run(p, args, stop)
    finally:
        for h in handlers:
            log.removeHandler(h)
            h.close()


def run(p: argparse.ArgumentParser, args: argparse.Namespace, stop: threading.Event | None) -> int:
    try:
        cameras = [parse_camera(v) for v in args.camera]
        if args.cameras_file:
            cameras += read_cameras_file(args.cameras_file)
        if args.token or args.rtsp:
            if not (args.token and args.rtsp):
                raise ValueError("--token and --rtsp go together")
            cameras.append(parse_camera(f"{args.token}={args.rtsp}"))
        if not cameras:
            raise ValueError("name at least one camera with --camera, --cameras-file or --token and --rtsp")
        bridge = Bridge(args.server, cameras, fps=args.fps, stop=stop)
    except (OSError, ValueError) as e:
        p.error(redact(str(e)))
    log.info("starting %d %s", len(cameras), "camera" if len(cameras) == 1 else "cameras")
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: bridge.stop.set())
    bridge.start()
    try:
        idle(bridge)
    except KeyboardInterrupt:
        pass
    bridge.close()
    return 0


def idle(bridge: "Bridge", remind_s: float = IDLE_REMIND_S) -> None:
    """Runs until stopped. Once every camera has stopped (its link replaced or removed), says so now and then and
    waits: exiting would let systemd or Docker restart the bridge, which would try the old links again."""
    said = None
    while not bridge.stop.is_set():
        if not bridge.running() and (said is None or time.monotonic() - said >= remind_s):
            said = time.monotonic()
            log.error("every camera has stopped. Put the new lines from the control room in the cameras file, then "
                      "restart the bridge.")
        bridge.stop.wait(1.0)


if __name__ == "__main__":
    sys.exit(main())
