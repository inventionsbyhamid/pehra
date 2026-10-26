"""Camera health: live, offline (no picture for Tuning health.offline_s), or covered, moved or blurred (the tamper
check in vision/tamper.py). Health is a signal like any other (rules/signals.py camera_health): the camera process
ticks the rules with it, and a watch on it alerts, such as every society's default "A camera is offline, covered,
turned or blurred" (rules/presets.py DEFAULT_WATCHES). The app judges health from what the camera processes report, so a camera whose
process died goes offline too; a change is written to the camera's row and resolves the open incidents of camera
health watches that no longer hold."""
import logging
import threading
import time
from typing import Literal

from watchful.tuning import HealthTuning

log = logging.getLogger("watchful.health")

HealthState = Literal["live", "offline", "covered", "moved", "blurred"]
TAMPER = ("covered", "moved", "blurred")
NO_PICTURE = ("offline", "covered")  # nothing in view can be judged: only watches about the camera itself run
BACK = "The camera is back."


def offline_after(camera, cfg: HealthTuning = HealthTuning()) -> float:
    """Tuning health.offline_s, or the camera's own override (a slow NVR)."""
    own = ((getattr(camera, "tuning", None) or {}).get("health") or {}).get("offline_s")
    return float(own) if own else cfg.offline_s


def check(camera, last_frame_at: float | None, tamper: str | None, now: float | None = None,
          cfg: HealthTuning = HealthTuning()) -> HealthState:
    """`last_frame_at`: wall clock of the camera's newest picture (or of its start, before the first); `tamper`: the
    tamper check's current problem, if any."""
    now = time.time() if now is None else now
    if last_frame_at is None or now - last_frame_at > offline_after(camera, cfg):
        return "offline"
    return tamper if tamper in TAMPER else "live"


def is_health_watch(watch) -> bool:
    """A watch only about the camera's own health: nothing in the picture for an AI to check."""
    return bool(watch.conditions) and all(c.signal == "camera_health" for c in watch.conditions)


def holds(watch, state: str) -> bool:
    """Whether a camera health watch is true for this health."""
    from watchful.rules.engine import compare
    passed = [c for c in watch.conditions if compare(state, c.op, c.value)]
    if watch.combine == "all":
        return len(passed) == len(watch.conditions)
    if watch.combine == "any":
        return bool(passed)
    return sum(c.weight for c in passed) >= watch.threshold


class HealthBook:
    """Every running camera's health as the app last judged it. `update` writes a change to the camera's row (a
    targeted UPDATE) and tells `on_change(workspace_id, camera_id, old, new, ts)`."""

    def __init__(self, repo_for, on_change=None):
        self.repo_for, self.on_change = repo_for, on_change
        self.states: dict[str, str] = {}
        self.since: dict[str, float] = {}  # when each camera's current state began
        self._lock = threading.Lock()

    def seed(self, camera_id: str, state: str | None) -> None:
        """A problem the camera's row still records from an earlier run of the app, so the camera coming back
        resolves what that run left open."""
        if state and state != "live":
            with self._lock:
                self.states.setdefault(camera_id, state)

    def state(self, camera_id: str) -> str | None:
        return self.states.get(camera_id)

    def update(self, workspace_id: str, camera_id: str, state: str, ts: float | None = None) -> bool:
        ts = time.time() if ts is None else ts
        with self._lock:
            old = self.states.get(camera_id)
            if old == state:
                return False
            self.states[camera_id], self.since[camera_id] = state, ts
        self._safely(self.repo_for(workspace_id).set_camera_health, camera_id, status=state)
        if self.on_change:
            self._safely(self.on_change, workspace_id, camera_id, old, state, ts)
        return True

    def forget(self, camera_id: str) -> str | None:
        with self._lock:
            self.since.pop(camera_id, None)
            return self.states.pop(camera_id, None)

    @staticmethod
    def _safely(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except Exception:  # a database or incident problem must not stop the other cameras' health
            log.exception("camera health: %s failed", getattr(fn, "__name__", fn))
            return None
