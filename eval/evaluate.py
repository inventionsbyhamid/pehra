"""Accuracy on labelled clips: precision, recall and F1 per watch, enter and exit timing, false alerts per
hour against a plain motion alarm, and how few frames reach the AI.

usage: python eval/evaluate.py [--ai] eval/labels/*.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

from eval_metrics import match_alerts, motion_baseline_alerts, timing_errors  # noqa: E402
from watchful.agent.verifier import verify  # noqa: E402
from watchful.components import make_llm  # noqa: E402
from watchful.config import Settings  # noqa: E402
from watchful.evidence import evidence_context, file_pack  # noqa: E402
from watchful.ingest.sources import FileSource  # noqa: E402
from watchful.rules.presets import preset  # noqa: E402
from watchful.rules.watch import Watch  # noqa: E402
from watchful.store.blobs import LocalBlobStore  # noqa: E402
from watchful.store.db import Camera, SqliteRepository  # noqa: E402
from watchful.tuning import Tuning  # noqa: E402
from watchful.vision.motion import MotionGate  # noqa: E402
from watchful.vision.scan import ScanPool, scan_file  # noqa: E402
from watchful.vision.zones import Zone  # noqa: E402


def watch_from(spec: dict) -> Watch:
    return preset(spec["preset"], **spec.get("fill", {})) if "preset" in spec else Watch.model_validate(spec)


def motion_flags(clip: str, fps: float) -> list[bool]:
    gate, src, out = MotionGate(), FileSource(clip, fps, start_epoch=0), []
    while (f := src.read()) is not None:
        out.append(gate.update(f.image).moving)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="+")
    ap.add_argument("--ai", action="store_true", help="check candidates with the AI like the live system")
    ap.add_argument("--out", default="eval/results/eval.json")
    a = ap.parse_args()
    settings = Settings(_env_file=ROOT / ".env") if (ROOT / ".env").exists() else Settings()
    tuning, tmp = Tuning(), ROOT / "data/eval-run"
    tmp.mkdir(parents=True, exist_ok=True)
    repo, blobs = SqliteRepository(tmp / "e.db"), LocalBlobStore(tmp)
    llm = make_llm(settings, repo, "verify") if a.ai else None
    report = {"clips": [], "totals": {}}
    totals: dict[str, list[int]] = {}
    with ScanPool(settings, tuning) as pool:
        for path in a.labels:
            lab = json.loads(Path(path).read_text())
            zones = [Zone(z["id"], z["name"], [tuple(p) for p in z["polygon"]]) for z in lab["zones"]]
            watches = [(f"w{i}", watch_from(s)) for i, s in enumerate(lab["watches"])]
            cam = Camera("eval", Path(path).stem, "file", lab["clip"], zones)
            r = scan_file(lab["clip"], cam, watches, pool, blobs, settings, tuning, start_epoch=0)
            kept = []
            for c in r.candidates:
                status = "alert"
                if llm is not None:
                    pack = file_pack(lab["clip"], 0, c, r.paths.get(c.track_id, []), r.poses.get(c.track_id, []), zones=zones)
                    status = verify(c, evidence_context(pack, repo, blobs), llm).status
                if status in ("alert", "unverified", "awaiting_human"):
                    kept.append(c)
            per_watch = {}
            for _, w in watches:
                m = match_alerts([c.ts for c in kept if c.watch_name == w.name], [tuple(x) for x in
                                                                                  lab["truth"].get(w.name, [])])
                per_watch[w.name] = {"tp": m.tp, "fp": m.fp, "fn": m.fn, "precision": m.precision,
                                     "recall": m.recall, "f1": m.f1,
                                     "false_alerts": [round(c.ts, 1) for c in kept if c.watch_name == w.name
                                                      and not any(a_ - 2 <= c.ts <= b + 2 for a_, b in
                                                                  lab["truth"].get(w.name, []))]}
                t = totals.setdefault(w.name, [0, 0, 0])
                t[0], t[1], t[2] = t[0] + m.tp, t[1] + m.fp, t[2] + m.fn
            found = [(e.kind, e.zone, e.ts) for e in r.events if e.kind in ("enter", "exit")]
            errs = timing_errors([tuple(x) for x in lab.get("enter_exit", [])], found)
            hours = r.stats.duration_s / 3600
            baseline = motion_baseline_alerts(motion_flags(lab["clip"], settings.analysis_fps), settings.analysis_fps)
            fps_total = sum(v["fp"] for v in per_watch.values())
            report["clips"].append({
                "clip": lab["clip"], "notes": lab.get("notes"), "watches": per_watch,
                "timing_error_s": {"mean": sum(errs) / len(errs) if errs else None, "max": max(errs, default=None),
                                   "matched": len(errs), "expected": len(lab.get("enter_exit", []))},
                "false_alerts_per_hour": fps_total / hours if hours else None,
                "motion_alarm_alerts_per_hour": baseline / hours if hours else None,
                "frames_analyzed": r.stats.frames_analyzed, "candidates": len(r.candidates),
                "share_of_frames_to_ai": len(r.candidates) / max(r.stats.frames_analyzed, 1),
                "scan": {"wall_s": r.stats.wall_s, "x_realtime": r.stats.x_realtime}})
    for name, (tp, fp, fn) in totals.items():
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        report["totals"][name] = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": rc,
                                  "f1": 2 * p * rc / (p + rc) if p + rc else 0.0}
    report["ai_checked"] = a.ai
    Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report["totals"], indent=2))


if __name__ == "__main__":
    main()
