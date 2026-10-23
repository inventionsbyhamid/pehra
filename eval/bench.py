"""Speed on this machine: live pipeline frames per second and seconds to scan a minute of 1080p video.

usage: python eval/bench.py --label graviton-cool --instance c8g.xlarge
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402

from watchful.config import Settings  # noqa: E402
from watchful.ingest.sources import FileSource  # noqa: E402
from watchful.rules.presets import preset  # noqa: E402
from watchful.store.blobs import LocalBlobStore  # noqa: E402
from watchful.store.db import Camera, SqliteRepository  # noqa: E402
from watchful.tuning import Tuning  # noqa: E402
from watchful.vision.perceive import Needs  # noqa: E402
from watchful.vision.pipeline import CameraPipeline  # noqa: E402
from watchful.vision.scan import ScanPool, scan_file  # noqa: E402
from watchful.vision.zones import Zone  # noqa: E402

LEFT = Zone("z1", "left", [(0, 0), (0.5, 0), (0.5, 1), (0, 1)])


def machine() -> dict:
    cpu = platform.processor()
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        cpu = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Model name")), cpu)
    except OSError:
        pass
    info = cv2.getBuildInformation()
    return {"host": socket.gethostname(), "arch": platform.machine(), "cpu": cpu, "cores": os.cpu_count(),
            "opencv": cv2.__version__, "kleidicv": "kleidicv" in info.lower(),
            "cool": Path("/opt/cool").exists()}


def live(settings: Settings, clip: Path, tmp: Path, seconds: float) -> dict:
    cam = Camera("bench", "bench", "file", str(clip), [LEFT])
    pipe = CameraPipeline(cam, SqliteRepository(tmp / "b.db"), LocalBlobStore(tmp), None, settings, Tuning(),
                          Needs(classes={"person"}, posture=True))
    src = FileSource(clip, settings.analysis_fps, start_epoch=0)
    start, n = time.perf_counter(), 0
    while (f := src.read()) is not None and f.ts < seconds:
        pipe.process(f)
        n += 1
    wall = time.perf_counter() - start
    stats = pipe.stats()
    return {"frames": n, "fps": n / wall, "ms_per_frame": stats["ms_per_frame"],
            "skipped_share": stats["frames_skipped"] / max(n, 1), "detector_runs": stats["detector_runs"]}


def scan(settings: Settings, clip: Path, tmp: Path, workers: int | None) -> dict:
    cam = Camera("bench", "bench", "file", str(clip), [LEFT])
    watches = [("w1", preset("enters", zone="left")), ("w2", preset("fall"))]
    with ScanPool(settings, Tuning(), workers) as pool:
        pool.warm()
        r = scan_file(clip, cam, watches, pool, LocalBlobStore(tmp), settings, Tuning(), start_epoch=0)
    return {"clip": clip.name, "duration_s": r.stats.duration_s, "wall_s": r.stats.wall_s,
            "x_realtime": r.stats.x_realtime, "seconds_per_minute": r.stats.wall_s / (r.stats.duration_s / 60),
            "workers": r.stats.workers, "stages": r.stats.stages}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--clip", default="data/samples/vtest.avi")
    ap.add_argument("--scan-clip", default="data/samples/speed_1080p.mp4")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--live-seconds", type=float, default=60)
    a = ap.parse_args()
    os.chdir(ROOT)
    if not Path(a.scan_clip).exists():
        subprocess.run(["bash", "eval/make_speed_clip.sh"], check=True)
    settings = Settings(demo_password="x", session_secret="y", _env_file=None)
    tmp = Path("data/bench")
    tmp.mkdir(parents=True, exist_ok=True)
    out = {"label": a.label, "machine": machine(), "detector": settings.detector, "dnn_engine": settings.dnn_engine,
           "live": live(settings, Path(a.clip), tmp, a.live_seconds),
           "scan": scan(settings, Path(a.scan_clip), tmp, a.workers)}
    prices = json.loads((ROOT / "eval/prices.json").read_text())
    if a.instance in prices:
        cameras = max(1.0, out["live"]["fps"] * out["machine"]["cores"] / settings.analysis_fps)
        out["cost"] = {"instance": a.instance, "usd_per_hour": prices[a.instance],
                       "cameras_per_instance": round(cameras, 1),
                       "usd_per_camera_hour": round(prices[a.instance] / cameras, 4)}
    dest = ROOT / "eval/results" / f"bench-{a.label}.json"
    dest.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
