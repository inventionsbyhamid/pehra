"""Metric math for the evaluation, kept small so it can be tested."""
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
