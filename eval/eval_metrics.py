"""Metric math for the evaluation, kept small so it can be tested."""
import math
from dataclasses import dataclass


@dataclass
class Match:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def match_alerts(alerts: list[float], truth: list[tuple[float, float]], tolerance_s: float = 2.0) -> Match:
    """An alert is right when it falls inside a true interval widened by the tolerance; each interval counts once."""
    hit, fp = set(), 0
    for t in sorted(alerts):
        found = next((i for i, (a, b) in enumerate(truth) if a - tolerance_s <= t <= b + tolerance_s), None)
        if found is None:
            fp += 1
        else:
            hit.add(found)
    return Match(len(hit), fp, len(truth) - len(hit))


def timing_errors(truth: list[tuple[str, str, float]], found: list[tuple[str, str, float]],
                  max_gap_s: float = 5.0) -> list[float]:
    """Seconds between each true enter or exit and the nearest found one of the same kind and zone."""
    errors = []
    for kind, zone, t in truth:
        same = [f for k, z, f in found if k == kind and z == zone and abs(f - t) <= max_gap_s]
        if same:
            errors.append(min(abs(f - t) for f in same))
    return errors


def motion_baseline_alerts(moving: list[bool], fps: float, quiet_s: float = 10.0) -> int:
    """What a plain motion alarm would send: one alert per motion start after quiet_s without motion."""
    alerts, quiet = 0, quiet_s * fps
    for m in moving:
        if m and quiet >= quiet_s * fps:
            alerts += 1
        quiet = 0 if m else quiet + 1
    return alerts


Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval for k of n (95% by default); None when n is 0."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n: int) -> float | None:
    """With no failures in n tries, the failure rate is at most 3/n (95%)."""
    return 3 / n if n > 0 else None


def proportion(k: int, n: int) -> dict:
    """k of n, its Wilson 95% interval, and with no failures (k == n > 0) the rule of three bound on failures."""
    interval = wilson(k, n)
    return {"k": k, "n": n, "value": round(k / n, 4) if n else None,
            "wilson95": [round(interval[0], 4), round(interval[1], 4)] if interval else None,
            "failures_at_most": round(rule_of_three(n), 4) if n and k == n else None}


def match_events(found: list[float], truth: list[float], tolerance_s: float = 2.0) -> list[tuple[int, int]]:
    """Pairs (found index, truth index), one to one, each within tolerance_s: the most pairs there can be. Truths in
    time order each take the earliest unpaired found time inside their window."""
    order = sorted(range(len(found)), key=lambda i: found[i])
    used: set[int] = set()
    pairs = []
    for j in sorted(range(len(truth)), key=lambda j: truth[j]):
        for i in order:
            if i in used or found[i] < truth[j] - tolerance_s:
                continue
            if found[i] > truth[j] + tolerance_s:
                break
            used.add(i)
            pairs.append((i, j))
            break
    return pairs


def percentile(values: list[float], q: float) -> float | None:
    """Linear interpolation between the closest ranks, as numpy's default."""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
