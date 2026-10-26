"""The gate and plate evaluation: exact, wrong and unread plates, and plate colour, with Wilson 95% intervals, by
day and night. Rows come from a CSV of reads already made (--rows), or the reader reads photos itself (--photos).

  python eval/plates.py --rows eval/labels/plates-commons.csv --name commons
  python eval/plates.py --photos DIR --name gate_day     DIR/truth.csv: photo,truth,colour_truth,light,in_grammar

A plate outside the grammar (old or special plates) should come back unread; it is counted on its own. Colour is right
when it matches; "unknown" is counted apart from a wrong colour. Results: eval/results/plates-<name>.json.
"""
import argparse
import csv
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_metrics import proportion  # noqa: E402
from harness import ROOT, engine_sha, git_sha  # noqa: E402

YES = {"yes", "true", "1"}


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    rows = []
    for row in csv.DictReader(lines):
        rows.append({"photo": row["photo"].strip(), "truth": row["truth"].strip().upper(),
                     "read": (row.get("read") or "").strip().upper() or None,
                     "colour_truth": (row.get("colour_truth") or "").strip().lower() or None,
                     "colour_read": (row.get("colour_read") or "").strip().lower() or "unknown",
                     "light": (row.get("light") or "day").strip().lower(),
                     "in_grammar": (row.get("in_grammar") or "yes").strip().lower() in YES})
    return rows


def read_photos(folder: Path) -> list[dict]:
    """The reader on each photo in folder/truth.csv, the whole photo as one crop."""
    import cv2
    from harness import run_settings
    from watchful.components import make_plate_reader
    from watchful.tuning import Tuning
    reader = make_plate_reader(run_settings(Path(tempfile.mkdtemp(prefix="plates-"))), Tuning())
    rows = read_rows(folder / "truth.csv")
    for row in rows:
        image = cv2.imread(str(folder / row["photo"]))
        if image is None:
            raise SystemExit(f"cannot read {folder / row['photo']}")
        got = reader.read([image])
        row["read"], row["colour_read"] = got.text, got.colour or "unknown"
    return rows


def score(rows: list[dict]) -> dict:
    """Counts and shares for one set of rows."""
    plates = [r for r in rows if r["in_grammar"]]
    others = [r for r in rows if not r["in_grammar"]]
    exact = sum(1 for r in plates if r["read"] == r["truth"])
    unread = sum(1 for r in plates if r["read"] is None)
    wrong = len(plates) - exact - unread
    coloured = [r for r in rows if r["colour_truth"]]
    right = sum(1 for r in coloured if r["colour_read"] == r["colour_truth"])
    unknown = sum(1 for r in coloured if r["colour_read"] == "unknown")
    return {"photos": len(rows), "plates": len(plates),
            "exact": exact, "wrong": wrong, "unread": unread,
            "exact_share": proportion(exact, len(plates)), "wrong_share": proportion(wrong, len(plates)),
            "unread_share": proportion(unread, len(plates)),
            "outside_grammar": {"photos": len(others), "unread": sum(1 for r in others if r["read"] is None),
                                "read_anyway": sum(1 for r in others if r["read"] is not None)},
            "colour": {"photos": len(coloured), "right": right, "unknown": unknown,
                       "wrong": len(coloured) - right - unknown, "accuracy": proportion(right, len(coloured))}}


def evaluate(rows: list[dict], source: str, about: str | None) -> dict:
    lights = sorted({r["light"] for r in rows})
    return {"source": source, "about": about, "code": git_sha(), "engine": engine_sha(),
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "all": score(rows),
            "by_light": {light: score([r for r in rows if r["light"] == light]) for light in lights},
            "rows": rows}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    given = ap.add_mutually_exclusive_group(required=True)
    given.add_argument("--rows", help="a CSV of reads already made")
    given.add_argument("--photos", help="a folder of photos with truth.csv")
    ap.add_argument("--name", required=True)
    ap.add_argument("--about", default=None)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    a = ap.parse_args(argv)
    if a.rows:
        path = Path(a.rows).resolve()
        rows, source = read_rows(path), path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        about = a.about or "".join(line[1:].strip() + " " for line in path.read_text().splitlines()
                                   if line.startswith("#")).strip() or None
    else:
        rows, source, about = read_photos(Path(a.photos)), f"{a.photos} (read by vision/plates.py now)", a.about
    result = evaluate(rows, source, about)
    dest = Path(a.out) / f"plates-{a.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=1) + "\n")
    s = result["all"]
    print(f"{s['exact']} exact, {s['wrong']} wrong, {s['unread']} unread of {s['plates']} plates; colour right on "
          f"{s['colour']['right']} of {s['colour']['photos']}; wrote {dest}")


if __name__ == "__main__":
    main()
