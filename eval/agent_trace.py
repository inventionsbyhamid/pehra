"""A real AI check with the app's own verifier, to show its OpenCV tools changing its next step.

One flagged moment from OpenCV's Plaza clip (vtest.avi): YOLOX and the tracker find the person whose feet come into
the demo's left walkway nearest the asked time, and the moment goes to agent/verifier.py exactly as a camera's would
(the evidence pack and tools of evidence.py, the watch "Someone in the left walkway", critical), with the configured
AI. With --dark the frames are dimmed first, as a camera at dusk sees them, so the model has a reason to look closer.
Each run's verdict and every step (tool, arguments, the model's reason, what the tool returned, milliseconds) go to
eval/results/agent-trace-<name>.json. It calls the real AI: a few requests a run.

  PYTHONPATH=backend python eval/agent_trace.py --name plaza --runs 2
  PYTHONPATH=backend python eval/agent_trace.py --name plaza_dark --dark 0.2 --runs 2
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from watchful.agent.llm import OpenRouterLLM  # noqa: E402
from watchful.agent.verifier import verify  # noqa: E402
from watchful.components import make_detector  # noqa: E402
from watchful.config import get_settings  # noqa: E402
from watchful.evidence import EvidencePack, evidence_context  # noqa: E402
from watchful.rules.engine import Candidate  # noqa: E402
from watchful.store.blobs import LocalBlobStore  # noqa: E402
from watchful.tuning import load_tuning  # noqa: E402
from watchful.vision.models import sample_path  # noqa: E402
from watchful.vision.track import Tracker  # noqa: E402

ZONE = [[0, 0.3], [0.5, 0.3], [0.5, 1], [0, 1]]  # the Plaza demo camera's left walkway (data/demo.json)
FPS, BEFORE_S, AFTER_S = 5.0, 4.0, 2.0


class NoRows:
    """The verifier reads only the society's place names from its repository; this check has none."""
    workspace_id = None

    def list_cameras(self):
        return []


def inside(box, w: int, h: int) -> bool:
    x, y, bw, bh = box
    fx, fy = (x + bw / 2) / w, (y + bh) / h
    return fx < ZONE[1][0] and fy > ZONE[0][1]


def moment(clip: Path, at: float, settings, tuning):
    """Frames around the asked time, and the path of the person who came into the walkway nearest to it."""
    cap = cv2.VideoCapture(str(clip), cv2.CAP_FFMPEG)
    detector, tracker = make_detector(settings, tuning, {"person"}), Tracker(tuning.track, first_id=1)
    frames, paths, entered = [], {}, {}
    t = max(0.0, at - BEFORE_S - 4.0)  # a few seconds more first, so tracks are settled at the window's start
    while t <= at + AFTER_S:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            break
        h, w = img.shape[:2]
        for tr in tracker.update(detector.detect(img), t, img).active:
            paths.setdefault(tr.id, []).append((t, tuple(float(v) for v in tr.box)))
            was = len(paths[tr.id]) > 1 and inside(paths[tr.id][-2][1], w, h)
            if inside(tr.box, w, h) and not was and tr.id not in entered:
                entered[tr.id] = t
        if t >= at - BEFORE_S:
            frames.append((t, img))
        t += 1 / FPS
    cap.release()
    if not entered:
        raise SystemExit(f"nobody came into the walkway near {at} s")
    track_id, ts = min(entered.items(), key=lambda kv: abs(kv[1] - at))
    return frames, [p for p in paths[track_id] if p[0] >= at - BEFORE_S], track_id, ts


def jpeg(img: np.ndarray, dark: float) -> bytes:
    if dark < 1:
        img = np.clip(img.astype(np.float32) * dark, 0, 255).astype(np.uint8)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def run(args) -> dict:
    settings, tuning = get_settings(), load_tuning()
    if not settings.openrouter_api_key:
        raise SystemExit("set OPENROUTER_API_KEY: this calls the real AI")
    clip = sample_path("vtest.avi", settings)
    clip = clip if clip.exists() else ROOT / "data" / "samples" / "vtest.avi"
    frames, path, track_id, ts = moment(clip, args.at, settings, tuning)
    blobs = LocalBlobStore(Path(tempfile.mkdtemp(prefix="agent-trace-")))
    near = min(frames, key=lambda f: abs(f[0] - ts))[1]
    x, y, bw, bh = (int(v) for v in min(path, key=lambda p: abs(p[0] - ts))[1])
    pad = int(0.15 * bh)
    blobs.put("plaza/kf/1.jpg", jpeg(near[max(0, y - pad):y + bh + pad, max(0, x - pad):x + bw + pad], args.dark),
              "image/jpeg")
    c = Candidate("w_walkway", "plaza", ts, track_id, "critical",
                  {"reasons": ["came into left walkway"], "signals": {"entered": True}}, ["plaza/kf/1.jpg"],
                  f"trace|{track_id}", "Someone in the left walkway", "Is a person walking into the left walkway?",
                  cls="person")
    pack = EvidencePack(c, [(t, jpeg(img, args.dark)) for t, img in frames], path, [], [("left walkway", ZONE)])
    llm = OpenRouterLLM(settings, model=settings.ai_model_verify or settings.openrouter_model)
    runs = []
    for _ in range(args.runs):
        v = verify(c, evidence_context(pack, NoRows(), blobs), llm)
        runs.append({"verdict": v.status, "summary": v.summary, "severity": v.severity, "reason": v.reason,
                     "tools": ", ".join(s.tool for s in v.trace),
                     "steps": [{"step": s.step, "tool": s.tool, "args": s.args, "why": s.why, "result": s.result,
                                "ms": s.ms} for s in v.trace]})
    try:
        code = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout
    except OSError:  # no git where it runs: the caller names the commit
        code = os.environ.get("TRACE_CODE", "")
    return {"about": "Real AI checks by the app's verifier (agent/verifier.py) on one flagged moment of OpenCV's Plaza "
                     "clip, made by eval/agent_trace.py. Not a scored evaluation: it shows what the model did with "
                     "its OpenCV tools on this moment.",
            "clip": "data/samples/vtest.avi", "watch": c.watch_name, "severity": c.severity, "asked_at_s": args.at,
            "moment_s": round(ts, 1), "track_id": track_id, "dark": args.dark,
            "model": f"{settings.ai_provider}: {llm.model}", "date": datetime.now(timezone.utc).isoformat(),
            "code": code.strip(), "runs": runs}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True, help="names the results file: eval/results/agent-trace-<name>.json")
    ap.add_argument("--at", type=float, default=40.0, help="seconds into the clip; the nearest walk in is taken")
    ap.add_argument("--dark", type=float, default=1.0, help="brightness kept, 0.2 for dusk; 1 leaves the frames alone")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args(argv)
    result = run(args)
    out = Path(args.out) / f"agent-trace-{args.name}.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    for r in result["runs"]:
        print(r["verdict"], r["severity"], [s["tool"] for s in r["steps"]], r["summary"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
