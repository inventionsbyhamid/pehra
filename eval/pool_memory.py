"""Memory and frame rate of the camera pool: N demo cameras (deploy/demo.json's first camera and its watches, looped
in real time) on the app's camera processes, with the app's settings from the environment (indexing, the scan pool
at start, the camera process count: their defaults unless set), but always a throwaway database and local storage.
The AI is a fake, so no AI calls are made. Prints RSS per process every 5 s, then each camera's analysed frames a second and frame latency, then
deploy/check_memory.py's verdict for the whole process tree.

usage: MALLOC_ARENA_MAX=2 python eval/pool_memory.py [--cameras 8] [--seconds 75] [--text-watch]
--text-watch adds a watch described in words, so the app makes text vectors (the SigLIP text tower)."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def tree(root: int) -> list[tuple[int, str, int]]:
    """(pid, command, RSS MB) of a process and everything it started."""
    info = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{name}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            status = dict(line.split(":", 1) for line in Path(f"/proc/{name}/status").read_text().splitlines()
                          if ":" in line)
        except OSError:
            continue
        info[int(name)] = (int(status["PPid"]), cmd, int(status.get("VmRSS", "0 kB").split()[0]) // 1024)
    keep, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid in info and pid not in keep:
            keep.add(pid)
            todo += [child for child, (parent, _, _) in info.items() if parent == pid]
    return sorted((pid, info[pid][1][:60], info[pid][2]) for pid in keep)


def main() -> None:
    args = argparse.ArgumentParser(prog="python eval/pool_memory.py")
    args.add_argument("--cameras", type=int, default=8)
    args.add_argument("--seconds", type=float, default=75)
    args.add_argument("--text-watch", action="store_true")
    a = args.parse_args()
    # a throwaway database, local storage and no metrics or messages, whatever this machine's app settings say
    os.environ.update(DATA_DIR=tempfile.mkdtemp(prefix="pool-memory-"), STORAGE="local", METRICS="log", NOTIFY="log",
                      SESSION_SECRET="pool-memory", DATA_KEY=os.urandom(32).hex())
    os.environ.pop("DATABASE_URL", None)
    from watchful.agent.llm import FakeLLM
    from watchful.components import make_blobs, make_engine
    from watchful.config import Settings
    from watchful.rules.presets import preset
    from watchful.store.db import Camera, WatchRow
    from watchful.store.sql import SqlRepository, create_workspace
    from watchful.tuning import load_tuning
    from watchful.vision.zones import Zone
    from watchful.workers import WorkerManager

    s = Settings()
    tuning = load_tuning(s.tuning_file)
    engine = make_engine(s)
    repo = SqlRepository(engine, create_workspace(engine, "Pool memory").id, secret=[s.data_key, *s.data_key_old])
    spec = json.loads((ROOT / "deploy" / "demo.json").read_text())["cameras"][0]
    clip = str((ROOT / spec["clip"]).resolve())
    cams = []
    for i in range(a.cameras):
        zones = [Zone(f"z{j}", z["name"], [tuple(p) for p in z["polygon"]]) for j, z in enumerate(spec["zones"])]
        cam = repo.add_camera(Camera(None, f"{spec['name']} {i}", "demo", clip, zones))
        watches = [preset(w["preset"], **w.get("fill", {})) for w in spec["watches"]]
        if a.text_watch:
            watches.append(preset("looks_like_check", zone=spec["zones"][0]["name"], text="someone carrying a box",
                                  not_text="someone with empty hands"))
        for w in watches:
            repo.add_watch(WatchRow(None, cam.id, w.name, w.model_dump(), True))
        cams.append(cam)
    m = WorkerManager(s, tuning, repo, make_blobs(s, tuning), llm=FakeLLM(['{"tool": "dismiss", "args": {}}'] * 9999))
    started = time.time()
    for cam in cams:
        m.start(cam.id)
    try:
        while time.time() - started < a.seconds:
            time.sleep(5)
            rows = tree(os.getpid())
            print(f"{time.time() - started:4.0f} s  {sum(r[2] for r in rows):5d} MB in {len(rows)} processes  "
                  + "  ".join(f"{r[2]}" for r in rows), flush=True)
        for cam in cams:
            st = m.status(cam.id)
            print(f"{cam.name}: process {st.process}, {st.fps:.2f} analysed fps, latency p50 "
                  f"{st.stats.get('latency_p50_ms')} p95 {st.stats.get('latency_p95_ms')} ms", flush=True)
        check = subprocess.run([sys.executable, str(ROOT / "deploy" / "check_memory.py"), "--pid", str(os.getpid()),
                                "--wait-s", "0"], capture_output=True, text=True)
        print(check.stdout.strip().splitlines()[-1] if check.stdout.strip() else check.stderr[-500:], flush=True)
    finally:
        m.shutdown()


if __name__ == "__main__":
    main()
