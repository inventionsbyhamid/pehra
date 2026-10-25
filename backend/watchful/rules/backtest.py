"""Backtest: replay a watch over the event index ("would have fired 3 times yesterday") before it goes live.
Only the Repository protocol is used. Signals the index cannot replay (they need pixels) are assumed and named."""
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from watchful.rules.engine import Candidate, RuleEngine, validate
from watchful.rules.signals import REGISTRY
from watchful.rules.watch import Condition, Watch
from watchful.tuning import BacktestTuning, Tuning
from watchful.vision.reason import Tick, TrackState

POSTURES = {"falling": "falling", "fallen": "fallen", "recovered": "upright"}
KINDS = ["track_start", "enter", "exit", "cross", "track_end", *POSTURES,
         "tamper_covered", "tamper_blurred", "tamper_moved", "tamper_restored"]
TIMED = {"dwell_s", "vanished_in_zone", "zones_visited"}
EPS = 1e-6


@dataclass
class BacktestResult:
    fires: int
    examples: list[str]
    cameras: int = 0
    events: int = 0
    not_replayed: list[str] = field(default_factory=list)  # signals that need pixels, so were assumed
    upper_bound: bool = False  # assumed true: "at most" this many
    lower_bound: bool = False  # assumed false: "at least" this many
    skipped: list[str] = field(default_factory=list)  # cameras the watch can't run on, and why
    capped: bool = False  # a busy minute had more events than one page; some were not replayed


def _replayable(w: Watch) -> tuple[Watch, list[str], str | None]:
    """The watch with unreplayable parts stood in for, the signals assumed, and which way they were assumed."""
    missing: list[str] = []
    assume = w.combine == "all"
    conditions = []
    for c in w.conditions:
        spec = REGISTRY.get(c.signal)
        if spec is not None and spec.replay:
            conditions.append(c)
        else:
            missing.append(c.signal)
            conditions.append(Condition(signal="_assume", params={"value": assume}, weight=c.weight))
    subject = w.subject
    if subject.looks_like or subject.same_as:
        missing.append("looks")
        subject = subject.model_copy(update={"looks_like": None, "looks_like_embedding": None, "same_as": None})
    replay = w.model_copy(update={"conditions": conditions, "subject": subject})
    way = None if not missing else ("upper" if assume or subject is not w.subject else "lower")
    return replay, missing, way


def _pages(repo, camera_id: str, t0: float, t1: float, cfg: BacktestTuning, capped: list[bool]) -> list:
    rows = repo.query_events(camera_id=camera_id, kinds=KINDS, since=t0, until=t1 - EPS, limit=cfg.page_limit)
    if len(rows) >= cfg.page_limit:
        if t1 - t0 > cfg.min_page_s:
            mid = (t0 + t1) / 2
            return _pages(repo, camera_id, t0, mid, cfg, capped) + _pages(repo, camera_id, mid, t1, cfg, capped)
        capped[0] = True
    return rows


def _events(repo, camera_id: str, since: float, until: float, cfg: BacktestTuning, capped: list[bool]) -> list:
    out, t = [], since
    while t < until:
        end = min(until, t + cfg.page_s)
        out += _pages(repo, camera_id, t, end, cfg, capped)
        t = end
    return sorted(out, key=lambda e: e.ts)


def _tail(w: Watch, tuning: Tuning) -> float:
    """How long after the last event time passing alone could still make the watch true."""
    longest = 0.0
    for c in w.conditions:
        longest = max(longest, float(c.params.get("hold_s") or 0))
        if c.signal == "vanished_in_zone":
            longest = max(longest, tuning.signals.vanish_hold_s, *tuning.signals.vanish_hold_by_kind.values())
        if c.signal in ("dwell_s",) and isinstance(c.value, (int, float)):
            longest = max(longest, float(c.value))
    return longest


def _timed(w: Watch) -> bool:
    return any(c.signal in TIMED or c.params.get("hold_s") for c in w.conditions)


def _box(e, fallback):
    box = (e.data or {}).get("box")
    return tuple(float(v) for v in box) if box else fallback


class _Replay:
    def __init__(self, w: Watch, cam, tuning: Tuning, tz: str, names: dict[str, str]):
        self.engine = RuleEngine([("backtest", w)], cam.zones, tuning, tz, "stream")
        self.cam, self.cfg, self.names = cam, tuning.backtest, names
        self.tracks: dict[int, TrackState] = {}
        self.size = (1, 1)
        self.timed, self.tail = _timed(w), _tail(w, tuning)
        self.out: list[Candidate] = []

    def _track(self, e) -> TrackState:
        t = self.tracks.get(e.track_id)
        if t is None:
            label = e.person_label
            t = self.tracks[e.track_id] = TrackState(e.track_id, e.cls or "person", _box(e, (0.0, 0.0, 0.0, 0.0)),
                                                     e.ts, e.ts, known_person_id=self.names.get(label, label),
                                                     known_name=label)
        t.box, t.last_ts = _box(e, t.box), e.ts
        if e.signature is not None:
            t.signature = e.signature
        return t

    def _tick(self, ts: float, events: list, ended: list) -> None:
        self.out += self.engine.on_tick(self.cam.id, Tick(ts, self.size, True, list(self.tracks.values()), events,
                                                          ended), ts)

    def _fill(self, start: float, until: float) -> None:
        """Ticks between events, so durations and holds can come true while nothing is logged."""
        if not self.timed:
            return
        t = start + self.cfg.step_s
        horizon = until if self.tracks else min(until, start + self.tail + self.cfg.step_s)
        while t < horizon:
            self._tick(t, [], [])
            t += self.cfg.step_s

    def run(self, rows: list, until: float) -> list[Candidate]:
        groups: dict[float, list] = {}
        for e in rows:
            groups.setdefault(e.ts, []).append(e)
        last = None
        for ts in sorted(groups):
            if last is not None:
                self._fill(last, ts)
            ended = []
            for e in groups[ts]:
                if e.track_id is None:
                    continue
                if e.kind == "track_start":
                    self.size = tuple((e.data or {}).get("size") or self.size)
                if e.kind == "track_end":
                    t = self._track(e)
                    zones = (e.data or {}).get("zones")
                    if zones is not None:
                        t.zones_inside = {z: t.zones_inside.get(z, ts) for z in zones}
                    ended.append(self.tracks.pop(e.track_id))
                    continue
                t = self._track(e)
                if e.kind == "enter" and e.zone:
                    t.zones_inside.setdefault(e.zone, ts)
                elif e.kind == "exit" and e.zone:
                    t.zones_inside.pop(e.zone, None)
                elif e.kind in POSTURES:
                    t.posture = POSTURES[e.kind]
            for tid in [k for k, t in self.tracks.items() if ts - t.last_ts > self.cfg.max_track_s]:
                self.tracks.pop(tid)
            self._tick(ts, groups[ts], ended)
            last = ts
        if last is not None:
            self._fill(last, until)
        return self.out


def _example(c: Candidate, cam, tz: str) -> str:
    local = datetime.fromtimestamp(c.ts, ZoneInfo(tz))
    when = f"{local:%a} {local.strftime('%I:%M %p').lstrip('0')}"
    reasons = ", ".join(c.evidence.get("reasons") or []) or c.watch_name
    return f"{when}, {cam.name}: {reasons}"


def backtest(watch: Watch, repo, camera_ids: list[str], since_s: float = 86400, *, now: float | None = None,
             tz: str = "Asia/Kolkata", tuning: Tuning | None = None) -> BacktestResult:
    tuning = tuning or Tuning()
    now = time.time() if now is None else now
    replay, missing, way = _replayable(watch)
    result = BacktestResult(0, [], not_replayed=sorted(set(missing)), upper_bound=way == "upper",
                            lower_bound=way == "lower")
    if replay.conditions and all(c.signal == "_assume" for c in replay.conditions):
        result.skipped.append("Nothing in this watch can be replayed from the saved events; it runs live only.")
        return result
    names = {e.name: e.person_id for e in repo.list_enrollments()}
    capped = [False]
    fires: list[tuple[Candidate, object]] = []
    for cid in camera_ids:
        cam = repo.get_camera(cid)
        if cam is None:
            continue
        problems = validate(replay, {z.name for z in cam.zones})
        if problems:
            result.skipped.append(f"{cam.name}: {'; '.join(problems)}")
            continue
        rows = _events(repo, cid, now - since_s, now, tuning.backtest, capped)
        result.cameras += 1
        result.events += len(rows)
        fires += [(c, cam) for c in _Replay(replay, cam, tuning, tz, names).run(rows, now)]
    fires.sort(key=lambda f: f[0].ts)
    result.fires = len(fires)
    result.examples = [_example(c, cam, tz) for c, cam in fires[:tuning.backtest.max_examples]]
    result.capped = capped[0]
    return result
