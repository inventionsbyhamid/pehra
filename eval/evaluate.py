"""Accuracy on labelled clips, through the live engine. Each clip plays through a camera process's own code
(pool.PoolWorker, in this process, every due frame analysed) at each analysis rate asked for, and every candidate goes
through the app's own path (WorkerManager: dedupe, incidents, the AI check, here a stand in that calls every moment
real and counts the checks).

Labels: eval/labels/<clip>.csv with columns t_s,event (seconds from the clip's start), and beside it
eval/labels/<clip>.json naming the clip, its zones and lines, and which watch answers which event (README there).
A candidate matches a label of its watch's event within the tolerance (2 s), one to one.

Per watch and rate: precision and recall with Wilson 95% intervals (and the rule of three when nothing failed), the
delay from the labelled moment to the candidate (median and p90), false alarms per camera hour; per rate: AI checks
and incidents, distinct person ids, and what a plain motion alarm would have sent. The first false alarm and the first
miss of each watch are saved as pictures with the tracker's boxes. Results: eval/results/eval-<clip>.json.

  python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1
"""
import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_metrics import match_events, motion_baseline_alerts, percentile, proportion  # noqa: E402
from harness import (ROOT, StandInAI, add_camera, clip_path, git_sha, machine, run_settings, society,  # noqa: E402
                     zones_of)

EVENT = re.compile(r"^[a-z][a-z0-9_]*$")


def load_labels(path: Path) -> list[tuple[float, str]]:
    """(t_s, event) rows from a t_s,event CSV; '#' starts a comment line."""
    rows = []
    with path.open(newline="") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or [h.strip() for h in reader.fieldnames][:2] != ["t_s", "event"]:
        raise SystemExit(f"{path}: the first line must be t_s,event")
    for n, row in enumerate(reader, 2):
        event = (row.get("event") or "").strip()
        try:
            t = float(row["t_s"])
        except (TypeError, ValueError):
            raise SystemExit(f"{path} row {n}: t_s is not a number") from None
        if t < 0 or not EVENT.match(event):
            raise SystemExit(f"{path} row {n}: need t_s >= 0 and an event in lower case words joined by _")
        rows.append((t, event))
    return sorted(rows)


def load_setup(labels: Path) -> dict:
    setup = json.loads(labels.with_suffix(".json").read_text())
    for key in ("clip", "zones", "watches"):
        if key not in setup:
            raise SystemExit(f"{labels.with_suffix('.json')}: missing {key}")
    if any("event" not in w for w in setup["watches"]):
        raise SystemExit("every watch in the setup names the event it answers")
    return setup


def score(fired: list[float], truth: list[float], tolerance_s: float, hours: float) -> dict:
    """One watch at one rate: candidate times against labelled times."""
    pairs = match_events(fired, truth, tolerance_s)
    tp = len(pairs)
    delays = [fired[i] - truth[j] for i, j in pairs]
    paired_f, paired_t = {i for i, _ in pairs}, {j for _, j in pairs}
    false = sorted(t for i, t in enumerate(fired) if i not in paired_f)
    missed = sorted(t for j, t in enumerate(truth) if j not in paired_t)
    return {"truth": len(truth), "fired": len(fired), "tp": tp, "fp": len(fired) - tp, "fn": len(truth) - tp,
            "precision": proportion(tp, len(fired)), "recall": proportion(tp, len(truth)),
            "delay_s": {"n": len(delays), "median": _round(percentile(delays, 50)), "p90": _round(percentile(delays, 90))},
            "false_alarms_per_camera_hour": round((len(fired) - tp) / hours, 1) if hours else None,
            "false_alarms_at": [round(t, 1) for t in false], "missed_at": [round(t, 1) for t in missed]}


def _round(v, digits: int = 2):
    return None if v is None else round(v, digits)


def replay(setup: dict, fps: float, work: Path) -> dict:
    """The clip through a camera process's code at `fps`, candidates through the app's path. Returns the candidates
    (watch id, clip seconds, track id), the tracker's boxes per analysed frame, and counts."""
    import cv2
    from watchful.alerts import incidents as incidents_
    from watchful.components import make_blobs
    from watchful.pool import CameraJob, PoolWorker
    from watchful.tuning import Tuning
    from watchful.workers import WorkerManager

    settings = run_settings(work, analysis_fps=fps, demo_realtime=False, index_embeddings=False,
                            scan_pool_at_start=False, camera_processes=1)
    tuning = Tuning()
    repo = society(settings)
    blobs = make_blobs(settings, tuning)
    ai = StandInAI()
    manager = WorkerManager(settings, tuning, repo, blobs, llm=ai)
    cam, watches = add_camera(repo, "Evaluation", "file", str(clip_path(setup["clip"])), zones_of(setup["zones"]),
                              setup["watches"])

    def send(msg: dict) -> None:
        if msg.get("type") in ("events", "log", "candidate"):
            manager._handle(msg)  # what WorkerManager._listen does with a camera process's message

    cv2.setNumThreads(settings.camera_threads)
    worker = PoolWorker(0, settings, tuning, send, blobs=blobs)
    worker.assign(CameraJob(cam, watches, [], {}, [], True, 0))
    runner = worker.cameras[cam.id]
    epoch = runner.source.start_epoch
    fired: list[tuple[str, float, int | None]] = []
    ticks: list[tuple[float, tuple, list]] = []
    on_tick, after = runner.engine.on_tick, runner.after

    def record_candidates(*args, **kwargs):
        out = on_tick(*args, **kwargs)
        fired.extend((c.watch_id, c.ts - epoch, c.track_id) for c in out)
        return out

    def record_tracks(frame, arrived, result):
        tick = result.tick
        ticks.append((frame.ts - epoch, tick.size, [(s.track_id, s.cls, tuple(s.box)) for s in tick.tracks]))
        return after(frame, arrived, result)

    runner.engine.on_tick, runner.after = record_candidates, record_tracks
    start = time.perf_counter()
    while cam.id in worker.cameras:
        worker.step()
    wall = time.perf_counter() - start
    deadline = time.monotonic() + 120
    while manager._inflight and time.monotonic() < deadline:  # checks still running in the app's check pool
        time.sleep(0.2)
    incidents = len(incidents_.list_incidents(repo, None, cam.id, limit=10000))
    manager.shutdown()
    worker.close()
    ids = {tid for _, _, tracks in ticks for tid, cls, _ in tracks if cls == "person"}
    return {"watches": watches, "fired": fired, "ticks": ticks, "frames": len(ticks), "wall_s": wall,
            "checks": ai.calls, "incidents": incidents, "person_ids": len(ids)}


def motion_alarm(clip: Path, fps: float) -> int:
    """What a plain motion alarm sends on this clip at this rate (a new alert after 10 s without motion)."""
    from watchful.ingest.sources import FileSource
    from watchful.vision.motion import MotionGate
    gate, src, moving = MotionGate(), FileSource(clip, fps, start_epoch=0), []
    while (f := src.read()) is not None:
        moving.append(gate.update(f.image).moving)
    return motion_baseline_alerts(moving, fps)


def picture(clip: Path, t: float, setup: dict, ticks: list, title: str, track: int | None, dest: Path) -> str:
    """The frame at t with the zones, the tracker's boxes at the nearest analysed frame, and the one in question."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * fps))
    ok, img = cap.read()
    cap.release()
    if not ok:
        return ""
    h, w = img.shape[:2]
    for z in setup["zones"]:
        pts = (np.array(z["polygon"]) * [w, h]).astype(np.int32)
        cv2.polylines(img, [pts], z.get("kind") != "line", (0, 220, 255) if z.get("kind") != "line" else (255, 200, 0), 2)
    if ticks:
        at, size, tracks = min(ticks, key=lambda k: abs(k[0] - t))
        sx, sy = w / max(size[0], 1), h / max(size[1], 1)
        for tid, cls, (x, y, bw, bh) in tracks:
            colour, thick = ((0, 0, 255), 3) if tid == track else ((255, 255, 255), 1)
            cv2.rectangle(img, (int(x * sx), int(y * sy)), (int((x + bw) * sx), int((y + bh) * sy)), colour, thick)
            cv2.putText(img, str(tid), (int(x * sx), int(y * sy) - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
        title += f" (boxes from {at:.1f} s)"
    cv2.rectangle(img, (0, 0), (w, 26), (0, 0, 0), -1)
    cv2.putText(img, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return dest.relative_to(ROOT).as_posix() if dest.is_relative_to(ROOT) else str(dest)


def evaluate(labels_path: Path, rates: list[float], out: Path, name: str) -> dict:
    import cv2
    setup = load_setup(labels_path)
    labels = load_labels(labels_path)
    clip = clip_path(setup["clip"])
    cap = cv2.VideoCapture(str(clip))
    duration = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 25.0)
    cap.release()
    skip, tol = float(setup.get("skip_start_s", 0.0)), float(setup.get("tolerance_s", 2.0))
    hours = max(duration - skip, 0.0) / 3600
    events = [w["event"] for w in setup["watches"]]
    unknown = sorted({e for _, e in labels} - set(events))
    if unknown:
        raise SystemExit(f"labels name events no watch answers: {', '.join(unknown)}")
    truth = {e: [t for t, ev in labels if ev == e and t >= skip] for e in events}
    pictures = out / "failures"
    result = {"clip": setup["clip"], "about": setup.get("about"), "labelled_by": setup.get("labelled_by"),
              "labels": labels_path.relative_to(ROOT).as_posix() if labels_path.is_relative_to(ROOT) else str(labels_path),
              "code": git_sha(), "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "machine": machine(),
              "duration_s": round(duration, 1), "skip_start_s": skip, "scored_s": round(duration - skip, 1),
              "tolerance_s": tol, "truth": {e: len(v) for e, v in truth.items()}, "runs": {}}
    for fps in rates:
        work = Path(tempfile.mkdtemp(prefix="eval-"))
        try:
            r = replay(setup, fps, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        tag = f"fps_{fps:g}"
        run = {"analysis_fps": fps, "frames": r["frames"], "wall_s": round(r["wall_s"], 1),
               "ms_per_frame": round(1000 * r["wall_s"] / max(r["frames"], 1)), "person_ids": r["person_ids"],
               "watches": {}}
        totals = [0, 0, 0]
        for (wid, watch), spec in zip(r["watches"], setup["watches"]):
            event = spec["event"]
            mine = [(t, track) for w_, t, track in r["fired"] if w_ == wid and t >= skip]
            s = score([t for t, _ in mine], truth[event], tol, hours)
            s["watch"] = watch.name
            for kind, times in (("first_false_alarm", s["false_alarms_at"]), ("first_miss", s["missed_at"])):
                if not times:
                    s[kind] = None
                    continue
                t = times[0]
                track = next((tr for tt, tr in mine if abs(tt - t) < 0.05), None) if kind == "first_false_alarm" \
                    else None
                what = "false alarm" if kind == "first_false_alarm" else "missed"
                dest = pictures / f"{name}-{fps:g}fps-{event}-{kind.replace('_', '-')}.jpg"
                s[kind] = {"t_s": t, "image": picture(clip, t, setup, r["ticks"], f"{fps:g} fps, {event}, {what} at "
                                                      f"{t:.1f} s", track, dest)}
            totals = [totals[0] + s["tp"], totals[1] + s["fp"], totals[2] + s["fn"]]
            run["watches"][event] = s
        tp, fp, fn = totals
        run["all"] = {"tp": tp, "fp": fp, "fn": fn, "precision": proportion(tp, tp + fp),
                      "recall": proportion(tp, tp + fn),
                      "false_alarms_per_camera_hour": round(fp / hours, 1) if hours else None}
        candidates = sum(1 for _, t, _ in r["fired"] if t >= 0)
        run["ai"] = {"candidates": candidates, "checks": r["checks"], "incidents": r["incidents"],
                     "checks_per_camera_hour": round(r["checks"] / (duration / 3600), 1) if duration else None,
                     "note": "one clip of %.0f s, shorter than an incident's grouping window (Tuning alerts.group_s), "
                             "so later repeats join the first incident without a check" % duration}
        run["motion_alarm_alerts"] = motion_alarm(clip, fps)
        result["runs"][tag] = run
        print(f"{name} at {fps:g} fps: " + ", ".join(
            f"{e} P {v['precision']['value']} R {v['recall']['value']}" for e, v in run["watches"].items()),
              file=sys.stderr, flush=True)
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("labels", nargs="+", help="eval/labels/<clip>.csv, each with its <clip>.json beside it")
    ap.add_argument("--fps", default="10,2,1", help="analysis rates to score, comma separated")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--name", default=None, help="results file eval-<name>.json (default: the labels file's name)")
    a = ap.parse_args(argv)
    import logging
    logging.basicConfig(level=logging.WARNING)
    rates = [float(x) for x in a.fps.split(",") if x.strip()]
    out = Path(a.out)
    for path in a.labels:
        labels = Path(path).resolve()
        name = a.name or labels.stem
        result = evaluate(labels, rates, out, name)
        dest = out / f"eval-{name}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
