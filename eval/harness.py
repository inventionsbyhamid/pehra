"""What the benchmark and the evaluation share: settings for a throwaway run, a society with cameras and watches,
the AI check stand in, and where the machine and the code come from."""
import json
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from watchful.config import Settings, available_cpus  # noqa: E402
from watchful.rules.presets import preset  # noqa: E402
from watchful.rules.watch import Watch  # noqa: E402
from watchful.store.db import Camera, WatchRow  # noqa: E402
from watchful.vision.zones import Zone  # noqa: E402

DEMO = ROOT / "deploy" / "demo.json"
RUN_MARK = "EVAL_RUN"  # in the environment of every process a run starts, so its watcher can find them

# Outside the run: waits for the run's process to end however it ends (finished, stopped, killed, out of memory),
# then kills every process still carrying the run's mark. Processes started by anything else lack the mark.
WATCHER = r"""
import os, signal, sys, time
parent, mark = int(sys.argv[1]), sys.argv[2].encode()

def state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None

def marked():
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) != os.getpid():
            try:
                with open(f"/proc/{name}/environ", "rb") as f:
                    if mark in f.read().split(b"\0"):
                        yield int(name)
            except OSError:
                pass

while state(parent) not in (None, "Z", "X"):
    time.sleep(1)
for _ in range(3):
    for pid in marked():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)
"""
REAL = json.dumps({"tool": "raise_alert", "args": {"summary": "Checked: it is happening."}, "why": "stand in"})


class StandInAI:
    """Takes the place of the AI check: every flagged moment is real and answered at once. Counts the checks, so a
    run reports how many moments would reach the AI."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, system, messages, images=(), json_schema=None, timeout_s=20) -> str:
        with self._lock:
            self.calls += 1
        return REAL


def guard_children(name: str) -> str | None:
    """Every process this run starts ends with it, on every way out. SIGTERM and SIGHUP unwind the run, so its finally
    blocks stop the camera pool; and a small watcher, outside the run, kills whatever still carries the run's mark once
    the run's process is gone, which covers SIGKILL and the out of memory killer too. Linux only (it reads /proc);
    returns the mark, or None where there is no /proc."""
    if not Path("/proc/self/environ").exists():
        return None
    mark = f"{RUN_MARK}={name}-{os.getpid()}-{secrets.token_hex(4)}"
    os.environ[RUN_MARK] = mark.split("=", 1)[1]
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, _: sys.exit(128 + signum))
    env = {k: v for k, v in os.environ.items() if k != RUN_MARK}
    subprocess.Popen([sys.executable, "-I", "-c", WATCHER, str(os.getpid()), mark], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mark


def run_settings(data_dir: Path, **changes) -> Settings:
    """The app's defaults (so indexing on and the real models), with throwaway secrets and this run's data folder."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(_env_file=None, session_secret=secrets.token_hex(32), data_key=secrets.token_hex(32),
                    data_dir=data_dir, **changes)


def society(settings: Settings, name: str = "Evaluation"):
    from watchful.components import make_engine, make_repo
    from watchful.store.sql import create_workspace
    return make_repo(settings, create_workspace(make_engine(settings), name).id)


def zones_of(spec: list[dict]) -> list[Zone]:
    return [Zone(z.get("id") or f"z{i}", z["name"], [tuple(p) for p in z["polygon"]], z.get("kind", "area"))
            for i, z in enumerate(spec)]


def watch_of(spec: dict) -> Watch:
    if "preset" in spec:
        return preset(spec["preset"], **spec.get("fill", {}))
    return Watch.model_validate(spec["watch"])


def add_camera(repo, name: str, kind: str, clip: str, zones: list[Zone], watches: list[dict]):
    """A camera with its watches in the society; returns it and [(watch id, Watch)] in the given order."""
    cam = repo.add_camera(Camera(None, name, kind, clip, zones))
    rows = []
    for spec in watches:
        w = watch_of(spec)
        row = repo.add_watch(WatchRow(None, cam.id, w.name, w.model_dump(), True))
        rows.append((row.id, w))
    return cam, rows


def demo_camera(name: str = "Plaza (OpenCV sample)") -> dict:
    return next(c for c in json.loads(DEMO.read_text())["cameras"] if c["name"] == name)


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def git_sha() -> str | None:
    """The commit this runs (GIT_SHA where the tree has no .git, as in the dev container)."""
    return _git("rev-parse", "--short", "HEAD") or os.environ.get("GIT_SHA")


def engine_sha() -> str | None:
    """The last commit that changed the app's code (backend/watchful): the engine a number measured."""
    return _git("log", "-1", "--format=%h", "--", "backend/watchful") or os.environ.get("ENGINE_SHA")


def machine() -> dict:
    import cv2
    cpu = platform.processor()
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        cpu = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("Model name")), cpu)
        flags = next((line.split(":", 1)[1].split() for line in out.splitlines() if line.startswith("Flags")), [])
    except OSError:
        flags = []
    info = cv2.getBuildInformation()
    cool_root = os.path.realpath(os.environ.get("COOL_ROOT", "/opt/cool"))  # as deploy/cv2_info.py tells COOL
    return {"host": socket.gethostname(), "arch": platform.machine(), "cpu": cpu, "avx2": "avx2" in flags,
            "cpus_available": available_cpus(), "host_cores": os.cpu_count(), "python": platform.python_version(),
            "opencv": cv2.__version__, "kleidicv": "kleidicv" in info.lower(),
            "cool": os.path.realpath(cv2.__file__).startswith(cool_root)}


def clip_path(name: str) -> Path:
    path = Path(name)
    return path if path.is_absolute() else ROOT / path
