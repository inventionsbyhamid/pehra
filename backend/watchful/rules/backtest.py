"""Backtest: replay a watch over the event index ("would have fired 3 times yesterday") before it goes live.
Only the Repository protocol is used. Signals the index cannot replay (they need pixels) are assumed to have held,
named, and the result is then counted in visits ("about 2 visits, assuming staying still held")."""
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
APPROXIMATE = {"alone": "Being alone is judged from where people were when their events were saved, so it is "
                        "approximate."}
LOOKS = "looks"  # the subject was picked by a description or a picture, which needs pixels
EPS = 1e-6


@dataclass
class BacktestResult:
    fires: int
    examples: list[str]
    visits: int = 0  # distinct tracks (or moments, for scene watches) it fired on
    summary: str = ""
    cameras: int = 0
    events: int = 0
    assumed: list[str] = field(default_factory=list)  # signals that need pixels, assumed to have held
    approximate: list[str] = field(default_factory=list)  # signals replayed only roughly
    skipped: list[str] = field(default_factory=list)  # cameras the watch can't run on, and why
    capped: bool = False  # too many events, or out of time: part of the day was not replayed


class _OutOfTime(Exception):
    pass


def _replayable(w: Watch) -> tuple[Watch, list[str]]:
    """The watch with the parts that need pixels assumed to hold, and those parts' signals."""
    assumed: list[str] = []
    conditions = []
    for c in w.conditions:
        spec = REGISTRY.get(c.signal)
        if spec is not None and spec.replay:
            conditions.append(c)
        else:
            assumed.append(c.signal)
            conditions.append(Condition(signal="_assume", params={"value": True}, weight=c.weight))
    subject = w.subject
    if subject.looks_like or subject.same_as:
        assumed.append(LOOKS)
        subject = subject.model_copy(update={"looks_like": None, "looks_like_embedding": None, "same_as": None})
    return w.model_copy(update={"conditions": conditions, "subject": subject}), sorted(set(assumed))


def _label(signal: str) -> str:
    return "the description" if signal == LOOKS else REGISTRY[signal].label if signal in REGISTRY else signal


def _summary(r: "BacktestResult") -> str:
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"

    if r.assumed:
        text = f"About {n(r.visits, 'visit')}, assuming {' and '.join(_label(s) for s in r.assumed)} held."
    else:
        text = f"Would have fired {n(r.fires, 'time')}."
    text += "".join(f" {APPROXIMATE[s]}" for s in r.approximate)
    if r.capped:
        text += " Only part of the day could be replayed in time, so the count is low."
    return text


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
    def __init__(self, w: Watch, cam, tuning: Tuning, tz: str, names: dict[str, str], deadline: float):
        self.engine = RuleEngine([("backtest", w)], cam.zones, tuning, tz, "stream")
        self.cam, self.cfg, self.names, self.deadline = cam, tuning.backtest, names, deadline
        self.tracks: dict[int, TrackState] = {}
        self.size = (1, 1)
        self.timed, self.tail = _timed(w), _tail(w, tuning)
        self.window, self.tz = w.time_window, ZoneInfo(w.time_window.tz or tz) if w.time_window else None
        self.out: list[Candidate] = []
        self.ticks = 0

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
        self.ticks += 1
        if self.ticks % 1000 == 0 and time.perf_counter() > self.deadline:
            raise _OutOfTime
        self.out += self.engine.on_tick(self.cam.id, Tick(ts, self.size, True, list(self.tracks.values()), events,
                                                          ended), ts)

    def _next_in_window(self, t: float) -> float:
        """t if the watch's hours include it, else the start of its next hours (so idle hours cost nothing)."""
        if self.window is None:
            return t
        offset = datetime.fromtimestamp(t, self.tz).utcoffset().total_seconds()
        local = (t + offset) % 86400
        start = int(self.window.start[:2]) * 3600 + int(self.window.start[3:]) * 60
        end = int(self.window.end[:2]) * 3600 + int(self.window.end[3:]) * 60
        inside = start <= local < end if start <= end else local >= start or local < end
        return t if inside else t + (start - local) % 86400

    def _fill(self, start: float, until: float) -> None:
        """Ticks between events, so durations and holds can come true while nothing is logged."""
        if not self.timed:
            return
        step = self.cfg.step_s
        horizon = until if self.tracks else min(until, start + self.tail + step)
        t = start + step
        while t < horizon:
            ahead = self._next_in_window(t)
            if ahead > t:
                t = start + step * -(-(ahead - start) // step)  # back onto the step grid
                continue
            self._tick(t, [], [])
            t += step

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
    deadline = time.perf_counter() + tuning.backtest.max_seconds
    replay, assumed = _replayable(watch)
    result = BacktestResult(0, [], assumed=assumed,
                            approximate=sorted({c.signal for c in watch.conditions} & set(APPROXIMATE)))
    if replay.conditions and all(c.signal == "_assume" for c in replay.conditions):
        result.skipped.append("Nothing in this watch can be replayed from the saved events; it runs live only.")
        result.summary = "This watch can't be tried on yesterday's events; it runs live only."
        return result
    names = {e.name: e.person_id for e in repo.list_enrollments()}
    capped = [False]
    fires: list[tuple[Candidate, object]] = []
    for cid in camera_ids:
        cam = repo.get_camera(cid)
        if cam is None:
            continue
        problems = validate(replay, {z.name: z.kind for z in cam.zones})
        if problems:
            result.skipped.append(f"{cam.name}: {'; '.join(problems)}")
            continue
        rows = _events(repo, cid, now - since_s, now, tuning.backtest, capped)
        result.cameras += 1
        result.events += len(rows)
        run = _Replay(replay, cam, tuning, tz, names, deadline)
        try:
            run.run(rows, now)
        except _OutOfTime:
            capped[0] = True
        fires += [(c, cam) for c in run.out]
        if capped[0] and time.perf_counter() > deadline:
            break
    fires.sort(key=lambda f: f[0].ts)
    result.fires = len(fires)
    result.visits = len({(cam.id, c.track_id if c.track_id is not None else c.ts) for c, cam in fires})
    result.examples = [_example(c, cam, tz) for c, cam in fires[:tuning.backtest.max_examples]]
    result.capped = capped[0]
    result.summary = _summary(result)
    return result
