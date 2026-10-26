"""What an AWS deploy showed, as eval/results/aws.json for the documents. deploy/deploy.sh runs it at the end with the
stack's outputs and the instance setup's output (remote_setup.sh's /healthz line, deploy/cv2_info.py and
deploy/check_memory.py), so the documents quote what the deploy itself checked.

    python3 deploy/aws_results.py --out eval/results/aws.json --region R --stack S --instance-type T --domain D
        --release SHA --outputs OUTPUTS.tsv --setup SETUP.txt [--build cool|stock]

--build stock (a blank COOL_AMI_ID) records cool false whatever the setup output says, so the documents never claim
COOL for a deploy on stock OpenCV.

A check whose line is missing from the setup output is left out of the file, so the documents say "not measured
yet" for it rather than guess. Standard library only, Python 3.9 or later: it runs on the laptop that deploys."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

CV2 = re.compile(r"^cv2 (\S+) from (\S+)", re.M)
COOL = re.compile(r"^COOL (yes|no):", re.M)
BUILD = re.compile(r"^opencv build: (.+)$", re.M)
MACHINE = re.compile(r"^machine: (\S+)$", re.M)
VIDEO = re.compile(r"^video (\S+) \(.*\): \d+ of \d+ frames decoded, (ok|FAIL)$", re.M)
MEMORY = re.compile(r"^memory: (\d+) MB in (\d+) processes, budget (\d+) MB: (ok|OVER)", re.M)


def outputs(text: str) -> dict:
    """The stack's outputs as `aws cloudformation describe-stacks ... --output text` prints them: key, tab, value."""
    pairs = (line.split("\t", 1) for line in text.splitlines() if "\t" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def parse_setup(text: str) -> dict:
    found: dict = {}
    for line in text.splitlines():
        if line.startswith("healthz {"):
            try:
                found["healthz"] = json.loads(line[len("healthz "):])
            except ValueError:
                pass
    if m := CV2.search(text):
        found["opencv"], found["cv2_file"] = m.group(1), m.group(2)
    if "KleidiCV loaded" in text or "KleidiCV not in this build" in text:
        found["kleidicv"] = "KleidiCV loaded" in text
    if m := COOL.search(text):
        found["cool"] = m.group(1) == "yes"
    if m := BUILD.search(text):
        found["opencv_build"] = m.group(1).strip()
    if m := MACHINE.search(text):
        found["machine"] = m.group(1)
    if videos := VIDEO.findall(text):
        found["video"] = {name: result == "ok" for name, result in videos}
    if m := MEMORY.search(text):
        found["memory_mb"], found["memory_processes"] = int(m.group(1)), int(m.group(2))
        found["memory_budget_mb"], found["memory_ok"] = int(m.group(3)), m.group(4) == "ok"
    return found


def results(region: str, stack: str, instance_type: str, domain: str, release: str, outputs_text: str,
            setup_text: str, now: datetime | None = None, build: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    data = {"format": 1, "written_by": "deploy/deploy.sh", "deployed_at": now.strftime("%Y-%m-%d %H:%M UTC"),
            "region": region, "stack": stack, "instance_type": instance_type, "code": release,
            "url": f"https://{domain}", "outputs": outputs(outputs_text), **parse_setup(setup_text)}
    if build:
        data["build"] = build
    if build == "stock":
        data["cool"] = False
        data["opencv_build"] = "stock OpenCV, not COOL"
    return data


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("out", "region", "stack", "instance-type", "domain", "release", "outputs", "setup"):
        args.add_argument(f"--{name}", required=True)
    args.add_argument("--build", choices=("cool", "stock"))
    opts = args.parse_args(argv)
    data = results(opts.region, opts.stack, opts.instance_type, opts.domain, opts.release,
                   Path(opts.outputs).read_text(), Path(opts.setup).read_text(), build=opts.build)
    out = Path(opts.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    main()
