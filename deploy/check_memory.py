"""The app's memory against its budget: the RSS of uvicorn (watchful.api), every process it started (the camera
worker processes, scan workers, their FFmpeg), a separate `python -m watchful.worker` and its children, summed. Fails
above the budget, which is Tuning resources.memory_budget_mb when Tuning has it, else a share of the machine's
memory that leaves the rest to the OS, Caddy, Litestream and the CloudWatch agent. It waits for the total to settle
first, as camera processes load their models after the app answers /healthz.
deploy/remote_setup.sh runs it after each deploy; run it any time with
    deploy/deploy.sh --run '/usr/local/bin/watchful-python deploy/check_memory.py'
--pid names the root processes instead (watchful-app on ioflood: --pid 1)."""
import argparse
import os
import sys
import time
from pathlib import Path

ROOTS = (b"watchful.api", b"watchful.worker")
SETTLE = 0.01  # the total moved less than this share over the last samples
SHARE_OF_MEMORY = 0.8  # without a Tuning budget: what the app may use of the machine's memory


def processes(roots: list[int] | None = None, proc: str = "/proc") -> list[tuple[int, str, float]]:
    """(pid, command, RSS MB) of the root processes and their descendants."""
    info: dict[int, tuple[int, str, float]] = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(f"{proc}/{name}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").strip()
            with open(f"{proc}/{name}/status") as f:
                status = dict(line.split(":", 1) for line in f if ":" in line)
        except OSError:
            continue
        rss = int(status.get("VmRSS", "0 kB").split()[0]) / 1024
        info[int(name)] = (int(status["PPid"]), cmd.decode(errors="replace"), rss)
    if roots is None:
        roots = [pid for pid, (_, cmd, _) in info.items() if any(r in cmd.encode() for r in ROOTS)]
    keep, frontier = set(), [p for p in roots if p in info]
    while frontier:
        pid = frontier.pop()
        if pid not in keep:
            keep.add(pid)
            frontier += [child for child, (parent, _, _) in info.items() if parent == pid]
    return sorted((pid, info[pid][1], info[pid][2]) for pid in keep)


def settled(roots: list[int] | None, wait_s: float, every_s: float = 2.0, samples: int = 3, proc: str = "/proc"):
    """The processes once their total has stopped moving, or after wait_s."""
    totals: list[float] = []
    deadline = time.monotonic() + wait_s
    while True:
        rows = processes(roots, proc)
        totals.append(sum(r[2] for r in rows))
        recent = totals[-samples:]
        if len(rows) > 1 and len(recent) == samples and max(recent) - min(recent) <= SETTLE * max(recent):
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(every_s)


def default_budget_mb(tuning=None, proc: str = "/proc") -> float:
    budget = getattr(getattr(tuning, "resources", None), "memory_budget_mb", None)
    if budget:
        return float(budget)
    with open(f"{proc}/meminfo") as f:
        total_kb = next(int(line.split()[1]) for line in f if line.startswith("MemTotal:"))
    return total_kb / 1024 * SHARE_OF_MEMORY


def load_tuning():
    try:
        from watchful.tuning import load_tuning as load
    except ImportError:
        return None
    tuning_file = os.environ.get("TUNING_FILE")
    return load(Path(tuning_file) if tuning_file else None)


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(prog="python deploy/check_memory.py")
    args.add_argument("--budget-mb", type=float, help="default Tuning resources.memory_budget_mb, else a share of RAM")
    args.add_argument("--pid", type=int, action="append", help="a root process (default: found by name)")
    args.add_argument("--wait-s", type=float, default=180.0, help="longest wait for the total to settle")
    args.add_argument("--proc", default="/proc", help=argparse.SUPPRESS)
    a = args.parse_args(argv)
    budget = a.budget_mb if a.budget_mb is not None else default_budget_mb(load_tuning(), a.proc)
    rows = settled(a.pid, a.wait_s, proc=a.proc)
    if not rows:
        print("memory: no app process is running")
        return 1
    total = sum(r[2] for r in rows)
    for pid, cmd, rss in rows:
        print(f"memory: {rss:7.0f} MB  {pid:>7}  {cmd[:90]}")
    ok = total <= budget
    print(f"memory: {total:.0f} MB in {len(rows)} processes, budget {budget:.0f} MB: {'ok' if ok else 'OVER'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
