"""The live pool under load. N cameras play a clip in real time through the app's own WorkerManager and its shared
camera processes, with the app's default settings: indexing on, the scan pool warmed at start, a watch with a
description (so the image model runs on crops and its text tower in the app), the real models, and a stand in for the
AI check that calls every flagged moment real. After a warm up it measures, for --seconds:

  analysed frames a second per camera and per vCPU, frame latency p50 and p95 (frame in to rules done, each camera's
  last frames), the memory of the app and of every process it started, cameras per instance at 2 and at 5 analysed
  frames a second, and the compute cost per camera a month from eval/prices.json.

The host load is sampled all along and written next to the numbers. Results go to eval/results/bench-<setup>.json.

  python eval/bench.py --setup devbox --cameras 8 --cpus 2
  python eval/bench.py --setup c8g_cool --instance c8g.large --cameras 12 --confirm    (eval/bench_aws.sh runs this)
"""
import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (ROOT, StandInAI, add_camera, demo_camera, engine_sha, git_sha, guard_children,  # noqa: E402
                     machine, run_settings, society, zones_of)

sys.path.insert(0, str(ROOT / "deploy"))
FLOORS = (2.0, 5.0)  # analysed frames a second a camera must get: the design's floor, and the default analysis_fps
TEXT_WATCH = {"preset": "lookout", "fill": {"text": "a person in a red jacket"}}
LOOP_CLIP = "data/samples/vtest_x4.avi"  # vtest.avi four times over (318 s), so no camera reaches its end
DEVBOX = "dev box (2013 Xeon, no AVX2, shared host)"


def load_prices(path: Path = ROOT / "eval" / "prices.json") -> dict:
    """The price file with the plans file's rupee rate, the one the site uses."""
    prices = json.loads(path.read_text())
    plans = json.loads((ROOT / "backend" / "watchful" / "data" / "plans.json").read_text())
    prices["usd_inr"] = plans["usd_inr"]
    return prices


def instance_cost(prices: dict, instance: str, cameras: int, cool: bool) -> dict:
    """What one instance costs a month (on demand, its public IPv4 and its EBS volume, the COOL fee on Graviton with
    COOL), shared by the cameras it can watch. Compute only: AI checks, storage and transfer are not in it."""
    inst, hours = prices["instances"][instance], prices["hours_per_month"]
    fee = prices["software"]["cool"]["usd_per_hour"].get(instance, 0.0) if cool and instance.startswith("c8g") else 0.0
    ebs = prices["ebs"]["gp3_gb_month"] * prices["ebs"]["volume_gb"] / hours
    hourly = inst["usd_per_hour"] + fee + prices["public_ipv4"]["usd_per_hour"] + ebs
    month = hourly * hours
    per_camera = month / cameras if cameras >= 1 else None
    rate = prices["usd_inr"]["rate"]
    return {"instance": instance, "vcpus": inst["vcpus"], "cameras": cameras, "cool_fee_usd_per_hour": fee,
            "usd_per_month": round(month, 2),
            "usd_per_camera_month": None if per_camera is None else round(per_camera, 2),
            "inr_per_camera_month": None if per_camera is None else round(per_camera * rate)}


def capacity(total_fps: float, vcpus: int, target_vcpus: int, floor: float) -> int:
    """Whole cameras that each get `floor` analysed frames a second from this throughput, on target_vcpus."""
    return math.floor(total_fps / vcpus * target_vcpus / floor + 1e-9)


def spread(values: list[float], digits: int = 2) -> dict:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {"n": len(values), "median": round(statistics.median(values), digits),
            "min": round(min(values), digits), "max": round(max(values), digits)}


def costs(total_fps: float, vcpus: int, prices: dict, instance: str | None, cool: bool) -> tuple[dict, dict]:
    """Cameras per instance at each floor, and the cost per camera a month on every priced instance."""
    capacities, cost = {}, {}
    for floor in FLOORS:
        key = f"at_{floor:g}fps"
        capacities[key] = capacity(total_fps, vcpus, vcpus, floor)
        rows = {}
        for name, inst in prices["instances"].items():
            n = capacity(total_fps, vcpus, inst["vcpus"], floor)
            here = name == instance  # elsewhere, as deployed: Graviton with COOL
            rows[name.replace(".", "_")] = {**instance_cost(prices, name, n, cool if here else True), "measured": here}
        cost[key] = rows
    return capacities, cost


def recost(result: dict, prices: dict) -> dict:
    """A results file's capacity and cost again from its measured throughput, with today's prices."""
    capacities, cost = costs(result["fps"]["total"], result["scenario"]["vcpus"], prices, result.get("instance"),
                             bool(result["machine"].get("cool")))
    return {**result, "capacity": capacities, "cost": cost,
            "prices": {"file": "eval/prices.json", "usd_inr": prices["usd_inr"]["rate"],
                       "checked": {k: v["checked"] for k, v in prices["instances"].items()}}}


def summarize(frames0: dict, frames1: dict, elapsed_s: float, latency: dict, memory: dict, vcpus: int,
              prices: dict, instance: str | None, cool: bool) -> dict:
    """The measured window as the results file holds it. frames0 and frames1: analysed frames per camera at the
    window's start and end; latency: camera id to (p50, p95) ms; memory: from Sampler.memory(). Cost rows for an
    instance this did not run on are marked measured false: they apply this machine's rate per vCPU."""
    per_camera = [(frames1[c] - frames0.get(c, 0)) / elapsed_s for c in frames1]
    total = sum(per_camera)
    per_vcpu = total / vcpus if vcpus else None
    p50 = [v[0] for v in latency.values() if v[0] is not None]
    p95 = [v[1] for v in latency.values() if v[1] is not None]
    out = {
        "fps": {"per_camera": spread(per_camera), "total": round(total, 2),
                "per_vcpu": None if per_vcpu is None else round(per_vcpu, 2)},
        "latency_ms": {"p50": spread(p50, 0)["median"], "p95": spread(p95, 0)["median"],
                       "p95_worst_camera": spread(p95, 0)["max"]},
        "memory_mb": memory}
    out["capacity"], out["cost"] = costs(total, vcpus, prices, instance, cool)
    return out


class Sampler:
    """Every few seconds: the host load and the memory of this process and everything it started."""

    def __init__(self, roles, every_s: float = 2.0, guard_mb: float = 0):
        from check_memory import processes
        self._processes, self.roles, self.every_s = processes, roles, every_s
        self.guard_mb = guard_mb  # stop the run when the container's memory in use passes this (a shared host)
        self.over: str | None = None
        self.loads: list[float] = []
        self.rows: list[tuple[float, list]] = []
        self.cpu: list[tuple[float, dict, float | None]] = []
        self.window: tuple[float, float] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(10)

    def _run(self) -> None:
        while not self._stop.is_set():
            now, rows = time.monotonic(), self._processes([os.getpid()])
            self.loads.append(os.getloadavg()[0])
            self.rows.append((now, rows))
            self.cpu.append((now, {pid: cpu_s(pid) for pid, _, _ in rows}, throttled_s()))
            used = container_mb()
            if self.guard_mb and used is not None and used > self.guard_mb:
                self.over = f"the container holds {used:.0f} MB, over {self.guard_mb:.0f} MB: stopped, others need it"
            self._stop.wait(self.every_s)

    def wait(self, seconds: float) -> None:
        """Sleeps, unless the memory guard trips."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.over:
                raise SystemExit(self.over)
            time.sleep(min(0.5, max(0.0, end - time.monotonic())))

    def cpu_use(self) -> dict:
        """CPU seconds each role used in the measured window, as a share of the window, and how long the container's
        quota held it back meanwhile (other work in the same container counts against the same quota)."""
        inside = [c for c in self.cpu if self.window and self.window[0] <= c[0] <= self.window[1] + self.every_s]
        if len(inside) < 2:
            return {}
        (t0, a, th0), (t1, b, th1) = inside[0], inside[-1]
        roles: dict[str, float] = {}
        names = {pid: role for _, rows in self.rows for pid, cmd, _ in rows for role in [self.roles(pid, cmd)]}
        for pid, end in b.items():
            if end is not None:
                roles[names.get(pid, "other")] = roles.get(names.get(pid, "other"), 0.0) + end - (a.get(pid) or 0.0)
        span = t1 - t0
        return {"window_s": round(span, 1), "cpus_used": {r: round(v / span, 2) for r, v in sorted(roles.items())},
                "throttled_s": None if th0 is None or th1 is None else round(th1 - th0, 1)}

    def memory(self) -> dict:
        """Totals over the measured window (median and peak) and over the whole run (peak), the app's own, and the
        peak of each process by its role."""
        inside = [rows for t, rows in self.rows if self.window and self.window[0] <= t <= self.window[1]] or \
            [rows for _, rows in self.rows]
        totals = [sum(r[2] for r in rows) for rows in inside]
        app = [next((r[2] for r in rows if r[0] == os.getpid()), 0.0) for rows in inside]
        peak_all = max((sum(r[2] for r in rows) for _, rows in self.rows), default=0.0)
        by_pid: dict[int, dict] = {}
        for _, rows in self.rows:
            for pid, cmd, rss in rows:
                d = by_pid.setdefault(pid, {"pid": pid, "role": self.roles(pid, cmd), "max_mb": 0})
                d["max_mb"] = round(max(d["max_mb"], rss))
        camera = [d["max_mb"] for d in by_pid.values() if d["role"] == "camera process"]
        return {"total_median": round(statistics.median(totals)) if totals else None,
                "total_max": round(max(totals)) if totals else None, "total_max_whole_run": round(peak_all),
                "app_median": round(statistics.median(app)) if app else None,
                "camera_process_max": max(camera, default=None),
                "processes": sorted(by_pid.values(), key=lambda d: (d["role"], d["pid"]))}

    def host_load(self) -> dict:
        return {"load_1m": spread(self.loads), "host_cores": os.cpu_count(), **self.cpu_use()}


def say(text: str) -> None:
    print(f"bench: {text}", file=sys.stderr, flush=True)


def cpu_s(pid: int) -> float | None:
    """CPU seconds a process has used, user and system (Linux)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return None


def container_mb() -> float | None:
    """Memory the container's processes hold (cgroup v2 anon), not counting the page cache."""
    try:
        stat = dict(line.split() for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines())
        return int(stat["anon"]) / 2 ** 20
    except (OSError, KeyError, ValueError):
        return None


def throttled_s() -> float | None:
    """Seconds the container's CPU quota has held its processes back so far (cgroup v2), when there is a quota."""
    try:
        stat = dict(line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines())
        return round(int(stat["throttled_usec"]) / 1e6, 1)
    except (OSError, KeyError, ValueError):
        return None


def pin_cpus(n: int) -> list[int]:
    """Runs this process and every process it starts on the n least busy CPUs it may use (Linux)."""
    allowed = sorted(os.sched_getaffinity(0))
    if n <= 0 or n >= len(allowed):
        return allowed

    def idle() -> dict[int, int]:
        out = {}
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("cpu") and line[3].isdigit():
                parts = line.split()
                out[int(parts[0][3:])] = int(parts[4]) + int(parts[5])
        return out

    a = idle()
    time.sleep(1.0)
    b = idle()
    cores = sorted(allowed, key=lambda c: b.get(c, 0) - a.get(c, 0), reverse=True)[:n]
    os.sched_setaffinity(0, cores)
    return sorted(cores)


def loop_clip(clip: Path) -> Path:
    if not clip.exists():
        src = ROOT / "data" / "samples" / "vtest.avi"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-stream_loop", "3", "-i", str(src), "-c", "copy",
                        str(clip)], check=True)
    return clip


def run(cameras: int, processes: int, cpus: int, seconds: float, clip: Path, work: Path, text: bool, index: bool,
        warmup_max_s: float, prices: dict, instance: str | None, cool: bool, guard_mb: float = 0) -> dict:
    from watchful.components import make_blobs
    from watchful.tuning import Tuning
    from watchful.workers import WorkerManager

    settings = run_settings(work, camera_processes=processes, scan_workers=cpus, index_embeddings=index)
    tuning = Tuning()
    # each camera's latency percentiles cover its last frames only, so the warm up is not in them
    tuning = tuning.model_copy(update={"resources": tuning.resources.model_copy(
        update={"latency_window": max(10, int(seconds))})})
    repo = society(settings, "Benchmark")
    ai = StandInAI()
    manager = WorkerManager(settings, tuning, repo, make_blobs(settings, tuning), llm=ai)
    plaza = demo_camera()
    watches = plaza["watches"] + ([TEXT_WATCH] if text else [])
    ids: list[str] = []

    def roles(pid: int, cmd: str) -> str:
        if pid == os.getpid():
            return "app"
        if any(p.get("pid") == pid for p in list(manager.processes.values())):
            return "camera process"
        if "ffmpeg" in cmd:
            return "clip encoder"
        if "resource_tracker" in cmd:
            return "multiprocessing helper"
        return "scan worker" if "multiprocessing" in cmd else "other"

    def frames() -> dict:
        return {c: (manager.status(c).stats or {}).get("frames", 0) for c in ids}

    sampler = Sampler(roles, guard_mb=guard_mb).start()
    started = time.monotonic()
    try:
        for i in range(cameras):
            cam, _ = add_camera(repo, f"Plaza {i + 1}", "file", str(clip), zones_of(plaza["zones"]), watches)
            ids.append(cam.id)
            manager.start(cam.id)
        while time.monotonic() - started < warmup_max_s and not all(frames().values()):
            sampler.wait(5)
            say(f"warming up: {sum(1 for n in frames().values() if n)} of {cameras} cameras analysing, "
                f"{time.monotonic() - started:.0f} s")
        ready_s = time.monotonic() - started
        if not all(frames().values()):
            raise SystemExit(f"cameras without a frame after {warmup_max_s:.0f} s: "
                             + ", ".join(c for c, n in frames().items() if not n))
        sampler.wait(10)  # every camera running: let the queues and the scan pool settle
        say(f"measuring {cameras} cameras for {seconds:.0f} s")
        t0, f0, calls0 = time.monotonic(), frames(), ai.calls
        sampler.window = (t0, t0 + seconds)
        sampler.wait(seconds)
        t1, f1 = time.monotonic(), frames()
        latency = {c: ((manager.status(c).stats or {}).get("latency_p50_ms"),
                       (manager.status(c).stats or {}).get("latency_p95_ms")) for c in ids}
        loads = {str(i): dict(p.get("loads", {})) for i, p in manager.processes.items()}
        checks = ai.calls - calls0
    finally:
        sampler.stop()
        try:
            manager.shutdown()
        finally:
            manager.pool.close()  # again, in case shutdown stopped short of it
    out = summarize(f0, f1, t1 - t0, latency, sampler.memory(), cpus, prices, instance, cool)
    out["timing"] = {"warmup_s": round(ready_s, 1), "settle_s": 10, "measured_s": round(t1 - t0, 1)}
    out["host"] = sampler.host_load()
    used = sum(out["host"].get("cpus_used", {}).values())
    # this host is shared: what the run actually got of its CPUs, and the rate per CPU it got
    out["fps"]["per_cpu_used"] = round(out["fps"]["total"] / used, 2) if used else None
    out["models_loaded"] = loads
    out["ai"] = {"checks": checks, "checks_per_camera_hour": round(checks / cameras / ((t1 - t0) / 3600), 1),
                 "note": "the same busy plaza on every camera, so this count is of that clip, not of a society "
                         "camera"}
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup", help="names the results file: devbox, c8g_cool, c8g_stock, c7i_stock")
    ap.add_argument("--label", default=None, help="how the docs name the machine")
    ap.add_argument("--instance", default=None, help="the priced instance this runs on (none on the dev box)")
    ap.add_argument("--cameras", type=int, default=8)
    ap.add_argument("--cpus", type=int, default=0, help="run on this many CPUs (Linux); 0: every one allowed")
    ap.add_argument("--processes", type=int, default=0, help="camera processes; 0: one per CPU")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup-max", type=float, default=240)
    ap.add_argument("--clip", default=LOOP_CLIP)
    ap.add_argument("--no-text", action="store_true", help="leave out the watch with a description")
    ap.add_argument("--no-index", action="store_true", help="indexing off (not the default)")
    ap.add_argument("--confirm", action="store_true",
                    help="run again with the cameras the first run says fit at 2 frames a second, and report what "
                         "each got")
    ap.add_argument("--memory-guard-mb", type=float, default=0,
                    help="on a shared host: stop the run when the container's memory in use passes this")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--recost", metavar="RESULTS", help="only work out a results file's capacity and cost again "
                                                          "from its measured rate and today's prices")
    a = ap.parse_args(argv)
    if a.recost:
        path = Path(a.recost)
        path.write_text(json.dumps(recost(json.loads(path.read_text()), load_prices()), indent=1) + "\n")
        print(f"wrote {path}")
        return
    if not a.setup:
        ap.error("--setup is required")
    if argv is None and os.environ.get("MALLOC_ARENA_MAX") is None:  # as the systemd units run the app
        os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, "MALLOC_ARENA_MAX": "2"})
    import logging
    logging.basicConfig(level=logging.WARNING)
    guard_children("bench")  # no camera process outlives the run, however it ends
    os.chdir(ROOT)
    cores = pin_cpus(a.cpus) if hasattr(os, "sched_setaffinity") else []
    cpus = len(cores) or a.cpus or os.cpu_count() or 1
    processes = a.processes or cpus
    prices = load_prices()
    info = machine()
    cool = bool(info["cool"])  # cv2 loaded from COOL: its fee is in the cost
    clip = loop_clip(ROOT / a.clip)
    work = Path(tempfile.mkdtemp(prefix="bench-", dir=os.environ.get("DATA_DIR") or None))
    label = a.label or (DEVBOX if a.setup.startswith("devbox") else a.setup)
    result = {"setup": a.setup, "label": label, "target": a.instance is not None, "instance": a.instance,
              "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": git_sha(),
              "engine": engine_sha(),
              "machine": info,
              "scenario": {"cameras": a.cameras, "processes": processes, "vcpus": cpus, "cores": cores,
                           "clip": a.clip, "watches": [w.get("preset") for w in demo_camera()["watches"]]
                           + ([] if a.no_text else ["lookout with a description"]),
                           "index_embeddings": not a.no_index, "scan_pool_at_start": True,
                           "ai": "stand in: every check real, answered at once", "analysis_fps": 5.0,
                           "camera_threads": 1, "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"),
                           "command": "python eval/bench.py " + " ".join(argv if argv is not None else sys.argv[1:])}}
    try:
        result.update(run(a.cameras, processes, cpus, a.seconds, clip, work / "run", not a.no_text, not a.no_index,
                          a.warmup_max, prices, a.instance, cool, a.memory_guard_mb))
        if a.confirm and result["capacity"]["at_2fps"] >= 1:
            n = result["capacity"]["at_2fps"]
            again = run(n, processes, cpus, a.seconds, clip, work / "confirm", not a.no_text, not a.no_index,
                        a.warmup_max, prices, a.instance, cool, a.memory_guard_mb)
            result["confirm"] = {"cameras": n, "fps": again["fps"], "latency_ms": again["latency_ms"],
                                 "memory_mb": {k: v for k, v in again["memory_mb"].items() if k != "processes"},
                                 "host": again["host"],
                                 "meets_2fps": (again["fps"]["per_camera"]["min"] or 0) >= 2.0}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result["prices"] = {"file": "eval/prices.json", "usd_inr": prices["usd_inr"]["rate"],
                        "checked": {k: v["checked"] for k, v in prices["instances"].items()}}
    dest = Path(a.out) / f"bench-{a.setup}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result.get(k) for k in ("setup", "fps", "latency_ms", "capacity", "host")}, indent=1))
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
