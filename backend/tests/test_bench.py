"""The benchmark, the evaluation and the documents' numbers: the metric math (Wilson intervals, the rule of three,
one to one matching), the cost per camera from its inputs, labels and their setup, the evaluation through the live
engine on a short clip, the AWS script that spends nothing without --yes-spend, and docs/fill.py, which lets no
number into the documents without a results file behind it."""
import importlib.util
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from eval_metrics import match_events, percentile, proportion, rule_of_three, wilson

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fill = _module("docs_fill", ROOT / "docs" / "fill.py")


# metric math

def test_wilson_interval_and_the_rule_of_three():
    lo, hi = wilson(9, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.596, 0.982)
    lo, hi = wilson(10, 10)
    assert round(lo, 3) == 0.722 and hi == pytest.approx(1.0)
    lo, hi = wilson(0, 5)
    assert lo == 0.0 and round(hi, 3) == 0.434
    assert wilson(0, 0) is None and rule_of_three(0) is None
    assert rule_of_three(30) == pytest.approx(0.1)
    p = proportion(10, 10)
    assert p["value"] == 1.0 and p["failures_at_most"] == 0.3 and p["wilson95"] == [0.7225, 1.0]
    assert proportion(9, 10)["failures_at_most"] is None  # a failure was seen: no bound needed
    assert proportion(0, 0) == {"k": 0, "n": 0, "value": None, "wilson95": None, "failures_at_most": None}
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([], 90) is None


def test_matching_is_one_to_one_and_finds_the_most_pairs():
    # nearest first would pair 1.9 with 2.5 and leave both others alone; the most pairs is two
    assert match_events([1.9, 4.2], [0.5, 2.5], 2.0) == [(0, 0), (1, 1)]
    # two alerts for one event: one matches, the other is a false alarm
    assert match_events([10.0, 10.5], [10.2], 2.0) == [(0, 0)]
    assert match_events([5.0], [8.0], 2.0) == []


def test_score_counts_inside_the_window_and_times_the_delay():
    evaluate = _module("evaluate_mod", ROOT / "eval" / "evaluate.py")
    s = evaluate.score([2.5, 3.0, 10.4, 11.0, 30.0, 79.4, 80.0], [1.9, 4.0, 10.0, 40.0, 79.6], 2.0, lo=3.0, hi=78.5)
    # 2.5 is the alert for the moment at 1.9, before the window, so it is no false alarm; 3.0 matches 4.0 inside it;
    # 79.4 is the alert for 79.6, past it; 80.0 matches nothing, past it
    assert (s["truth"], s["tp"], s["fp"], s["fn"]) == (3, 2, 2, 1)
    assert s["false_alarms_at"] == [11.0, 30.0] and s["missed_at"] == [40.0]
    assert s["delay_s"] == {"n": 2, "median": -0.3, "p90": 0.26}
    assert s["false_alarms_per_camera_hour"] == round(2 / (75.5 / 3600), 1)
    assert s["precision"]["k"] == 2 and s["precision"]["n"] == 4 and s["recall"]["n"] == 3


def test_labels_read_with_comments_and_refuse_bad_rows(tmp_path):
    evaluate = _module("evaluate_mod", ROOT / "eval" / "evaluate.py")
    good = tmp_path / "a.csv"
    good.write_text("# made by eye\nt_s,event\n12.5,enter\n# a note\n3,cross_in\n")
    assert evaluate.load_labels(good) == [(3.0, "cross_in"), (12.5, "enter")]
    for text in ("time,event\n1,enter\n", "t_s,event\nx,enter\n", "t_s,event\n1,Enter now\n", "t_s,event\n-1,enter\n"):
        bad = tmp_path / "b.csv"
        bad.write_text(text)
        with pytest.raises(SystemExit):
            evaluate.load_labels(bad)


def test_the_plaza_labels_fit_their_setup():
    evaluate = _module("evaluate_mod", ROOT / "eval" / "evaluate.py")
    labels = evaluate.load_labels(ROOT / "eval" / "labels" / "plaza.csv")
    setup = evaluate.load_setup(ROOT / "eval" / "labels" / "plaza.csv")
    assert {e for _, e in labels} <= {w["event"] for w in setup["watches"]}
    assert "by eye" in setup["labelled_by"] and setup["tolerance_s"] == 2.0
    # the walkway edge is the zone's right edge, so every crossing in is also an entry at the same moment
    entries = {t for t, e in labels if e == "enter"}
    assert {t for t, e in labels if e == "cross_in"} <= entries
    assert all(0 <= t <= 79.5 for t, _ in labels)


# the benchmark's arithmetic

def test_cost_per_camera_adds_up_from_its_inputs():
    bench = _module("bench_mod", ROOT / "eval" / "bench.py")
    prices = bench.load_prices()
    rate = prices["usd_inr"]["rate"]
    assert bench.capacity(7.32, 2, 2, 2.0) == 3 and bench.capacity(7.32, 2, 4, 2.0) == 7
    assert bench.capacity(6.0, 2, 2, 2.0) == 3 and bench.capacity(0.4, 2, 2, 2.0) == 0
    c = bench.instance_cost(prices, "c8g.large", 3, cool=True)
    hourly = 0.05398 + 0.01 + 0.005 + 0.0912 * 40 / 730  # on demand, COOL, public IPv4, 40 GB gp3
    assert c["usd_per_month"] == round(hourly * 730, 2)
    assert c["usd_per_camera_month"] == round(hourly * 730 / 3, 2)
    assert c["inr_per_camera_month"] == round(hourly * 730 / 3 * rate)
    stock = bench.instance_cost(prices, "c8g.large", 3, cool=False)
    assert stock["cool_fee_usd_per_hour"] == 0.0 and stock["usd_per_month"] < c["usd_per_month"]
    assert bench.instance_cost(prices, "c7i.large", 3, cool=True)["cool_fee_usd_per_hour"] == 0.0  # no COOL on x86
    none = bench.instance_cost(prices, "c8g.large", 0, cool=True)
    assert none["usd_per_camera_month"] is None and none["inr_per_camera_month"] is None

    out = bench.summarize({"a": 10, "b": 12}, {"a": 70, "b": 66}, 60.0, {"a": (300.0, 500.0), "b": (320.0, 610.0)},
                          {"total_median": 1500}, 2, prices, None, False)
    assert out["fps"] == {"per_camera": {"n": 2, "median": 0.95, "min": 0.9, "max": 1.0}, "total": 1.9,
                          "per_vcpu": 0.95}
    assert out["latency_ms"] == {"p50": 310.0, "p95": 555.0, "p95_worst_camera": 610.0}
    assert out["capacity"] == {"at_2fps": 0, "at_5fps": 0}
    row = out["cost"]["at_2fps"]["c8g_xlarge"]
    # a machine that is not the instance: its rate per vCPU on the instance's vCPUs, priced as deployed (COOL)
    assert row["measured"] is False and row["cameras"] == 1 and row["cool_fee_usd_per_hour"] == 0.01
    again = bench.recost({"fps": {"total": 7.32}, "scenario": {"vcpus": 2}, "instance": "c8g.large",
                          "machine": {"cool": True}}, prices)
    assert again["capacity"] == {"at_2fps": 3, "at_5fps": 1}
    assert again["cost"]["at_2fps"]["c8g_large"]["measured"] is True


def test_prices_name_their_source_and_day():
    prices = json.loads((ROOT / "eval" / "prices.json").read_text())
    assert prices["region"] == "ap-south-1"
    assert {"c8g.large", "c8g.xlarge", "c7i.large"} <= set(prices["instances"])
    rows = [*prices["instances"].values(), prices["software"]["cool"], prices["ebs"], prices["public_ipv4"]]
    assert all(row.get("source") and row.get("checked") for row in rows)
    assert all(row["usd_per_hour"] > 0 and row["vcpus"] in (2, 4) for row in prices["instances"].values())
    assert prices["software"]["cool"]["ami"]["ap-south-1"].startswith("ami-")


# the evaluation through the live engine

def test_evaluation_runs_the_live_engine_and_saves_failure_pictures(tmp_path):
    from watchful.vision.models import sample_path
    clip = tmp_path / "plaza10.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample_path("vtest.avi")), "-t", "10",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    setup = json.loads((ROOT / "eval" / "labels" / "plaza.json").read_text())
    setup.update(clip=str(clip), skip_start_s=1.0, skip_end_s=0.5)
    (tmp_path / "short.json").write_text(json.dumps(setup))
    # one real entry, and a crossing out the clip stops before (it happens at 10.3 s)
    (tmp_path / "short.csv").write_text("t_s,event\n5.9,enter\n9.0,cross_out\n")
    evaluate = _module("evaluate_mod", ROOT / "eval" / "evaluate.py")
    result = evaluate.evaluate(tmp_path / "short.csv", [2.0], tmp_path / "results", "short")
    run = result["runs"]["fps_2"]
    assert run["frames"] >= 18 and run["person_ids"] > 0
    assert result["truth"] == {"enter": 1, "cross_in": 0, "cross_out": 1}
    assert run["ai"]["candidates"] >= run["ai"]["checks"] >= 1  # candidates went through the app's path
    failures = 0
    for event, w in run["watches"].items():
        assert w["fired"] == w["tp"] + w["fp"] and w["truth"] == w["tp"] + w["fn"], event
        for kind, n in (("first_miss", w["fn"]), ("first_false_alarm", w["fp"])):
            assert (w[kind] is not None) == (n > 0), (event, kind)
            if n:  # the picture of the moment, with the tracker's boxes
                assert (tmp_path / "results" / w[kind]["image"]).is_file() and w[kind]["t_s"] == min(
                    w["missed_at"] if kind == "first_miss" else w["false_alarms_at"])
                failures += 1
    assert failures >= 1


# the AWS benchmark

FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
entry = {"args": args}
if "--user-data" in args:
    entry["user_data"] = Path(args[args.index("--user-data") + 1][7:]).read_text()
with open(os.environ["FAKE_AWS_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\n")
op = " ".join(args[:2])
itype = args[args.index("--instance-type") + 1] if "--instance-type" in args else ""
if op == "sts get-caller-identity":
    print("111122223333")
elif op == "s3 presign":
    print("https://bucket.s3.ap-south-1.amazonaws.com/bundle.tgz?X-Amz-Signature=abc&X-Amz-Expires=14400")
elif op == "ssm get-parameter":
    print("ami-0ubuntux86")
elif op == "ec2 describe-subnets":
    print("subnet-a\tsubnet-b")
elif op == "ec2 describe-images":
    print("/dev/sda1")
elif op == "ec2 run-instances":
    if itype == "c7i.large" and os.environ.get("FAKE_C7I_FAILS"):
        print("An error occurred (UnauthorizedOperation) when calling the RunInstances operation", file=sys.stderr)
        sys.exit(254)
    if itype == "c8g.large" and "subnet-a" in args:
        print("An error occurred (Unsupported) c8g.large is not supported in ap-south-1a", file=sys.stderr)
        sys.exit(254)
    print({"c8g.large": "i-0c8g", "c7i.large": "i-0c7i"}[itype])
elif op == "s3api list-objects-v2":
    print("done/c8g\tdone/c7i")
elif op == "ec2 describe-instances":
    print("running")
elif op == "s3 cp" and args[2].endswith("/results/"):
    Path(args[3]).mkdir(parents=True, exist_ok=True)
    Path(args[3], "bench-c8g_cool.json").write_text('{"setup": "c8g_cool"}')
elif op == "iam create-role":
    print("arn:aws:iam::111122223333:role/x")
'''


def aws_bench(tmp_path, *args, **extra):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "aws").write_text(FAKE_AWS)
    (fake / "aws").chmod(0o755)
    with tarfile.open(tmp_path / "code.tar", "w") as tar:
        tar.add(ROOT / "eval" / "bench_aws.sh", "eval/bench_aws.sh")
    (tmp_path / "results").mkdir(exist_ok=True)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE_AWS_LOG": str(tmp_path / "aws.log"),
           "BENCH_TARBALL": str(tmp_path / "code.tar"), "BENCH_RESULTS": str(tmp_path / "results"), "POLL_S": "0",
           "TMPDIR": str(tmp_path), **extra}
    out = subprocess.run(["bash", str(ROOT / "eval" / "bench_aws.sh"), "--params", str(tmp_path / "none.env"), *args],
                         env=env, capture_output=True, text=True, cwd=ROOT, timeout=120)
    log = tmp_path / "aws.log"
    return out, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_aws_bench_spends_only_when_told_and_terminates_what_it_started(tmp_path):
    assert subprocess.run(["bash", "-n", str(ROOT / "eval" / "bench_aws.sh")]).returncode == 0

    out, calls = aws_bench(tmp_path)
    assert out.returncode == 2, out.stderr
    assert "expected cost: at most $" in out.stdout and "--yes-spend" in out.stderr
    assert "c8g.large from the COOL AMI ami-05a336eb12c47d427" in out.stdout and "c7i.large" in out.stdout
    assert calls == []  # no AWS call at all

    out, calls = aws_bench(tmp_path, "--yes-spend", "--cameras", "10", "--seconds", "120")
    assert out.returncode == 0, out.stdout + out.stderr
    ops = [" ".join(c["args"][:2]) for c in calls]
    runs = [c for c in calls if c["args"][:2] == ["ec2", "run-instances"]]
    assert [r["args"][r["args"].index("--image-id") + 1] for r in runs] == [
        "ami-05a336eb12c47d427", "ami-05a336eb12c47d427", "ami-0ubuntux86"]  # c8g tried a second subnet
    c8g, c7i = runs[1]["user_data"], runs[2]["user_data"]
    assert "shutdown -h +75" in c8g and "BENCH_SETUPS='c8g_cool c8g_stock'" in c8g
    assert "shutdown -h +55" in c7i and "BENCH_SETUPS='c7i_stock'" in c7i
    assert "BENCH_INSTANCE='c8g.large'" in c8g and "BENCH_CAMERAS='10'" in c8g and "BENCH_SECONDS='120'" in c8g
    assert "X-Amz-Signature=abc&X-Amz-Expires=14400'" in c8g and c8g.rstrip().endswith("shutdown -h now")
    assert all("terminate" in r["args"][r["args"].index("--instance-initiated-shutdown-behavior") + 1] for r in runs)
    stop = calls[ops.index("ec2 terminate-instances")]["args"]
    assert stop[stop.index("--instance-ids") + 1:stop.index("--instance-ids") + 3] == ["i-0c8g", "i-0c7i"]
    assert {"iam delete-role", "iam delete-instance-profile", "s3 rb"} <= set(ops)
    assert ops.index("ec2 terminate-instances") > ops.index("s3 cp", ops.index("ec2 run-instances"))
    assert (tmp_path / "results" / "bench-c8g_cool.json").is_file()

    # a failure after the first instance started still terminates it, and removes the role and the bucket
    (tmp_path / "aws.log").unlink()
    out, calls = aws_bench(tmp_path, "--yes-spend", FAKE_C7I_FAILS="1")
    assert out.returncode != 0
    ops = [" ".join(c["args"][:2]) for c in calls]
    stop = calls[ops.index("ec2 terminate-instances")]["args"]
    assert "i-0c8g" in stop and "i-0c7i" not in stop
    assert {"iam delete-role", "s3 rb"} <= set(ops)


# docs/fill.py

KEYS = {
    "eval.*.fps_*.*.recall": {"from": "eval-{1}", "path": "runs.fps_{2}.watches.{3}.recall.value", "unit": "pct",
                              "means": "recall"},
    "eval.*.fps_*.*.recall_ci": {"from": "eval-{1}", "path": "runs.fps_{2}.watches.{3}.recall.wilson95",
                                 "unit": "interval", "means": "its interval"},
    "eval.*.fps_*.all.recall": {"from": "eval-{1}", "path": "runs.fps_{2}.all.recall.value", "unit": "pct",
                                "means": "every watch"},
    "bench.*.fps_total": {"from": "bench:{1}", "path": "fps.total", "unit": "fps", "means": "throughput"},
    "bench.*.memory_total": {"from": "bench:{1}", "path": "memory_mb.total_median", "unit": "mb", "means": "memory"},
    "bench.speedup.cool_over_stock": {"ratio": ["bench.c8g_cool.fps_total", "bench.c8g_stock.fps_total"],
                                      "unit": "times", "means": "COOL over stock"},
    "plates.*.exact": {"from": "plates-{1}", "path": "all.exact", "unit": "int", "means": "exact reads"},
    "plans.society.month": {"from": "plans", "path": "plans.society.price_month_inr", "unit": "inr_whole",
                            "means": "a price"},
}


def _tree(tmp_path: Path) -> Path:
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    (results / "eval-plaza.json").write_text(json.dumps({"runs": {"fps_1": {
        "watches": {"enter": {"recall": {"value": 0.75, "wilson95": [0.5, 0.9]}},
                    "cross_out": {"recall": {"value": None}}},
        "all": {"recall": {"value": 0.8}}}}}))
    (results / "bench-devbox.json").write_text(json.dumps({"setup": "devbox", "fps": {"total": 7.32},
                                                           "memory_mb": {"total_median": 1574}}))
    (results / "bench-c8g-cool.json").write_text(json.dumps({"setup": "c8g_cool", "fps": {"total": 30.0}}))
    (results / "plates-commons.json").write_text(json.dumps({"all": {"exact": 5}}))
    data = tmp_path / "backend" / "watchful" / "data"
    data.mkdir(parents=True)
    (data / "plans.json").write_text(json.dumps({"plans": [{"id": "society", "price_month_inr": 699}]}))
    docs = tmp_path / "docs"
    (docs / "src").mkdir(parents=True)
    (docs / "keys.json").write_text(json.dumps(KEYS))
    (docs / "links.json").write_text(json.dumps({"demo": "https://demo.example", "repo": ""}))
    (docs / "src" / "report.md").write_text(
        "Entries at {{say:1 fps}}: {{eval.plaza.fps_1.enter.recall}} ({{eval.plaza.fps_1.enter.recall_ci}}), all "
        "{{eval.plaza.fps_1.all.recall}}, out {{eval.plaza.fps_1.cross_out.recall}}, at {{say:2 fps}} "
        "{{eval.plaza.fps_2.enter.recall}}.\n"
        "| Dev box | {{bench.devbox.fps_total|bare}} | {{bench.devbox.memory_total}} |\n"
        "COOL {{bench.speedup.cool_over_stock}}. Plates {{plates.commons.exact}}. {{plans.society.month}} a camera, "
        "matched within {{say:2 s}}. Demo {{link.demo}}, repo {{link.repo}}.\n")
    (tmp_path / "README.md").write_text("# App\n\n<!-- results start -->\nold\n<!-- results end -->\n")
    (docs / "src" / "readme-results.md").write_text("Entries: {{eval.plaza.fps_1.enter.recall}}.\n")
    return tmp_path


def test_numbers_come_linked_from_their_files_and_gaps_say_so(tmp_path):
    root = _tree(tmp_path)
    summary = fill.fill(root)
    report = (root / "docs" / "report.md").read_text()
    e = "../eval/results/eval-plaza.json"
    assert f'Entries at 1 fps: [75%]({e} "runs.fps_1.watches.enter.recall.value") ' \
           f'([50% to 90%]({e} "runs.fps_1.watches.enter.recall.wilson95")), all ' \
           f'[80%]({e} "runs.fps_1.all.recall.value"), out [n/a]({e} "runs.fps_1.watches.cross_out.recall.value"), ' \
           f'at 2 fps not measured yet.' in report  # the most specific key wins; a null is n/a; a rate not run waits
    assert '| Dev box | [7.3](../eval/results/bench-devbox.json "fps.total") | ' \
           '[1,574 MB](../eval/results/bench-devbox.json "memory_mb.total_median") |' in report
    assert "COOL not measured yet." in report  # one side of the ratio is missing
    assert 'Plates [5](../eval/results/plates-commons.json "all.exact").' in report
    plans = '[₹699](../backend/watchful/data/plans.json "plans.society.price_month_inr")'
    assert f"{plans} a camera, matched within 2 s." in report
    assert "Demo https://demo.example, repo not set yet." in report
    assert "{{" not in report and report.startswith("<!-- Made by docs/fill.py")
    assert '<!-- results start -->\nEntries: [75%](eval/results/eval-plaza.json ' in (root / "README.md").read_text()
    assert summary["unknown"] == [] and summary["unsourced"] == {}
    assert summary["missing"] == ["bench.speedup.cool_over_stock", "eval.plaza.fps_2.enter.recall", "link.repo"]


def test_unknown_keys_stop_it_and_numbers_without_a_source_are_listed(tmp_path, capsys):
    root = _tree(tmp_path)
    fill.main(["--root", str(root), "--check"])  # waiting is fine without --strict
    assert "waiting: eval.plaza.fps_2.enter.recall" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        fill.main(["--root", str(root), "--check", "--strict"])
    assert stop.value.code == 1
    (root / "docs" / "src" / "devpost.md").write_text(
        "We measured 94% recall, 380 ms, 1,574 MB and about ₹1,478 a camera on 2 vCPUs, `5 fps` in code, "
        "{{say:5 fps}} as set, and {{bench.devbox.fps_totl}} and {{eval.plaza.recall}}.\n")
    summary = fill.fill(root, check=True)
    assert summary["unsourced"] == {"devpost.md": ["94%", "380 ms", "1,574 MB", "₹1,478"]}
    assert summary["unknown"] == ["bench.devbox.fps_totl", "eval.plaza.recall"]
    assert not (root / "docs" / "report.md").exists()
    with pytest.raises(SystemExit):
        fill.main(["--root", str(root)])


def test_two_bench_files_of_one_setup_stop_the_run(tmp_path):
    root = _tree(tmp_path)
    (root / "eval" / "results" / "bench-again.json").write_text(json.dumps({"setup": "devbox"}))
    with pytest.raises(SystemExit, match="are both setup devbox"):
        fill.fill(root, check=True)


def _resolves(sources, key: str) -> str:
    return sources.value(key).status


def test_the_repo_keys_reach_every_results_file_and_the_docs_keep_the_rules():
    """Every key the documents may use resolves in each results file there is, so a renamed field in a results file
    fails here and not in front of a judge; and no number in a template comes from nowhere."""
    keys = json.loads((ROOT / "docs" / "keys.json").read_text())
    for key, spec in keys.items():
        assert spec.get("means") and spec.get("unit") in fill.UNITS, key
    sources = fill.Sources(ROOT)
    benches = [n[6:] for n in sources.files if n.startswith("bench:")]
    evals = {n[5:]: data for n, (_, data) in sources.files.items() if n.startswith("eval-")}
    plates = [n[7:] for n in sources.files if n.startswith("plates-")]
    for key, spec in keys.items():
        if key.startswith("bench.*.") and not spec.get("optional"):
            for setup in benches:
                assert _resolves(sources, key.replace("*", setup, 1).replace("*", "c8g_large")) in ("ok", "na"), \
                    (key, setup)
        if key.startswith("plates.*."):
            for name in plates:
                assert _resolves(sources, key.replace("*", name, 1)) in ("ok", "na"), (key, name)
        if key.startswith("eval.*."):
            for clip, data in evals.items():
                for rate, run in data["runs"].items():
                    for watch in run["watches"]:
                        k = key.replace("*", clip, 1).replace("fps_*", rate).replace("*", watch)
                        if "truth.*" in key:
                            k = key.replace("*", clip, 1).replace("*", watch)
                        assert _resolves(sources, k) in ("ok", "na"), (k, clip)
    summary = fill.fill(ROOT, check=True)
    assert summary["unknown"] == [], summary["unknown"]
    assert summary["unsourced"] == {}, summary["unsourced"]
