"""Plain words to a watch. The AI composes the generic building blocks; code then checks every part: the
catalog, spellings of zones and lines, setup tasks, coverage tiers and the privacy rule (no age, no children),
so nothing the catalog cannot express is saved as a watch that silently never fires."""
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import ValidationError

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.prompting import parse_json, render
from watchful.rules.engine import validate
from watchful.rules.presets import describe
from watchful.rules.signals import REGISTRY, TIERS, catalog, worse
from watchful.rules.vocab import DATA, resolve_classes, vocabulary
from watchful.rules.watch import Watch
from watchful.vision.detect import COCO

ATTEMPTS = 3
SCHEMA = {"type": "object", "properties": {
    "watch": {"type": ["object", "null"]},
    "coverage": {"type": "array", "items": {"type": "object"}},
    "setup": {"type": "array", "items": {"type": "string"}},
    "cannot": {"type": "array", "items": {"type": "object"}}},
    "required": ["watch", "coverage", "setup", "cannot"]}


@dataclass
class Coverage:
    clause: str
    signal: str
    tier: str  # "A" reliable, "B" approximate, "C" the AI checks every time, "X" not watched
    caveat: str = ""


@dataclass
class Cannot:
    clause: str
    reason: str
    alternative: str  # the nearest watchable request


@dataclass
class CompileContext:
    zones: dict[str, str] = field(default_factory=dict)  # name to kind: "area" or "water"
    lines: list[str] = field(default_factory=list)
    state_examples: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # zone to state to periods
    people: dict[str, str] = field(default_factory=dict)  # enrolled staff id to name
    camera_flags: dict = field(default_factory=dict)  # e.g. night_ir, plate_readable
    purpose: str | None = None

    def __post_init__(self):
        if not isinstance(self.zones, dict):
            self.zones = {z: "area" for z in self.zones}
        self.lines = list(self.lines) + [n for n, k in self.zones.items() if k == "line" and n not in self.lines]
        self.zones = {n: k for n, k in self.zones.items() if k != "line"}
        self.state_examples = {z: (s if isinstance(s, dict) else {state: [] for state in s})
                               for z, s in self.state_examples.items()}

    @property
    def names(self) -> list[str]:
        return list(self.zones) + list(self.lines)


@dataclass
class CompileResult:
    watch: Watch | None
    coverage: list[Coverage] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    cannot: list[Cannot] = field(default_factory=list)
    error: str | None = None

    @property
    def problem(self) -> str | None:
        """One sentence for callers that only show a problem."""
        if self.error or self.watch is not None:
            return self.error
        c = self.cannot[0] if self.cannot else None
        return f"{c.reason} I can watch for {c.alternative} instead." if c else "That can't be watched yet."


@lru_cache
def _words() -> dict:
    return json.loads((DATA / "compile.json").read_text())


@lru_cache
def _age_pattern() -> re.Pattern:
    terms = sorted(_words()["age_terms"], key=len, reverse=True)
    return re.compile(r"\b(?:(?:a|an|the|some|any)\s+)?(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I)


def mentions_age(text) -> bool:
    return bool(text) and _age_pattern().search(str(text)) is not None


def _neutral(text: str) -> str:
    out = re.sub(r"\s{2,}", " ", _age_pattern().sub("someone", text)).strip()
    return out[:1].upper() + out[1:]


def _explain(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
    return str(e).split("\n")[0]


def _clause(c: dict | object) -> str:
    params = c.get("params", {}) if isinstance(c, dict) else c.params
    signal = c.get("signal") if isinstance(c, dict) else c.signal
    return str(params.get("text") or signal)


def _strip_age(w: dict) -> tuple[dict | None, list[str]]:
    """Removes every part of an AI written watch that is about age or children. A watch whose subject was
    picked by age, or that has nothing left, is dropped."""
    removed, kept = [], []
    for c in w.get("conditions") or []:
        if mentions_age(json.dumps(c.get("params") or {})) or mentions_age(c.get("value")):
            removed.append(_clause(c))
        else:
            kept.append(c)
    subject = w.get("subject") or {}
    lost_subject = mentions_age(subject.get("looks_like"))
    if lost_subject:
        removed.append(subject.pop("looks_like"))
    w["conditions"] = kept
    if lost_subject or not kept:
        return None, removed or [w.get("name", "")]
    for key in ("name", "verify"):
        if mentions_age(w.get(key)):
            removed.append(w[key])
            w[key] = _neutral(w[key])
    return w, removed


def _cannot(items) -> list[Cannot]:
    out = []
    for c in items or []:
        if isinstance(c, dict) and (c.get("clause") or c.get("reason")):
            out.append(Cannot(str(c.get("clause") or ""), str(c.get("reason") or _words()["fallback_reason"]),
                              str(c.get("alternative") or "")))
    return out


def _fix_names(w: Watch, ctx: CompileContext) -> list[str]:
    """Zone and line names spelled like the drawn ones; the names still missing come back."""
    known = {n.lower(): n for n in ctx.names}

    def fix(name: str) -> str:
        return known.get(name.strip().lower(), name.strip())

    w.zones = [fix(z) for z in w.zones]
    missing = [z for z in w.zones if z not in ctx.names]
    for c in w.conditions:
        c.zones = [fix(z) for z in c.zones]
        missing += [z for z in c.zones if z not in ctx.names]
        if c.params.get("line"):
            c.params["line"] = fix(str(c.params["line"]))
            missing += [c.params["line"]] if c.params["line"] not in ctx.names else []
    return list(dict.fromkeys(missing))


def _ai_setup(item) -> bool:
    """Setup tasks the AI wrote are kept, except drawing and pictures: code works those out from the watch."""
    if not isinstance(item, str) or not item.strip() or mentions_age(item):
        return False
    low = item.strip().lower()
    return not (low.startswith("draw") or "picture" in low or "photo" in low)


def _setup(w: Watch, ctx: CompileContext, missing: list[str], given: list) -> list[str]:
    out = [s.strip() for s in given or [] if _ai_setup(s)]
    lines = {str(c.params.get("line") or (c.zones[0] if c.zones else "")) for c in w.conditions
             if c.signal == "crossed"}
    where = "on each camera it should run on" if w.scope == "site" else "on this camera"
    for name in missing:
        if name in lines:
            out.append(f"Draw a line called {name} where things cross, with the inside on its right.")
        else:
            out.append(f"Draw a zone called {name} {where}.")
    for c in w.conditions:
        if c.signal != "zone_state" or not c.zones:
            continue
        zone, state = c.zones[0], str(c.value).strip().lower()
        have = {s.lower(): set(p) for s, p in ctx.state_examples.get(zone, {}).items()}
        if state not in have:
            out.append(f"Save two pictures of the {zone} when it is {state}, by day and by night.")
        if not set(have) - {state}:
            out.append(f"Save two pictures of the {zone} when it is not {state}, by day and by night.")
        for s, periods in have.items():
            for period, words in (("day", "by day"), ("night", "at night")):
                if periods and period not in periods:
                    out.append(f"Save two pictures of the {zone} when it is {s} {words}.")
    return list(dict.fromkeys(out))


def _coverage(w: Watch, given, ctx: CompileContext) -> list[Coverage]:
    out: list[Coverage] = []
    for item in given or []:
        if not isinstance(item, dict) or mentions_age(item.get("clause")) or "output" in str(item.get("signal")):
            continue
        signal, tier = str(item.get("signal") or ""), str(item.get("tier") or "B").upper()
        tier = tier if tier in TIERS else "B"
        spec = REGISTRY.get(signal)
        caveat = str(item.get("caveat") or "")
        if spec is not None:
            tier, caveat = worse(tier, spec.tier), caveat or spec.caveat
        out.append(Coverage(str(item.get("clause") or signal), signal, tier, caveat))
    covered = {c.signal for c in out}
    for c in w.conditions if w else []:
        if c.signal not in covered:
            spec = REGISTRY[c.signal]
            out.append(Coverage(_clause(c), c.signal, spec.tier, spec.caveat))
            covered.add(c.signal)
    if w and w.subject.who not in ("anyone",) and "faces" not in covered:
        out.append(Coverage(w.subject.who if w.subject.who not in ("known", "unknown") else f"{w.subject.who} people",
                            "faces", "B", "Faces are named only for enrolled staff seen clearly from the front."))
    if ctx.camera_flags.get("night_ir") and w:
        colours = re.compile(r"\b(" + "|".join(_words()["colour_words"]) + r")\b", re.I)
        coloured = {c.signal for c in w.conditions if colours.search(json.dumps([c.params, c.value]).replace("_", " "))}
        for cov in out:
            if cov.signal in coloured and "night" not in cov.caveat.lower():
                cov.caveat = (cov.caveat + " " + _words()["night_colour_caveat"]).strip()
                cov.tier = worse(cov.tier, "B")
    return out


def _contradictions(w: Watch) -> list[str]:
    """Conditions that can never all be true at once, which would make a watch that never fires."""
    if w.combine != "all":
        return []
    wanted: dict[tuple, set] = {}
    for c in w.conditions:
        if c.op in ("is", "==") and isinstance(c.value, str):
            key = (c.signal, tuple(c.zones), str(c.params.get("name") or c.params.get("line") or ""))
            wanted.setdefault(key, set()).add(c.value.strip().lower())
    return [f"{k[0]} can't be {' and '.join(sorted(v))} at once; use combine any or op in"
            for k, v in wanted.items() if len(v) > 1]


def _asks_to_log(text: str) -> bool:
    words = "|".join(re.escape(w) for w in _words()["log_words"])
    return re.search(rf"\b({words})\b", text, re.I) is not None


def _settle_overlaps(coverage: list[Coverage], cannot: list[Cannot]) -> list[Cannot]:
    """A part that is watched is not also a part that can't be: its limit becomes the coverage caveat."""
    watched = [(c.clause.strip().lower(), c) for c in coverage if c.tier != "X"]
    kept = []
    for c in cannot:
        clause = c.clause.strip().lower()
        cov = next((v for k, v in watched if clause and (clause in k or k in clause)), None)
        if cov is None or mentions_age(c.clause):
            kept.append(c)
        elif c.reason not in cov.caveat:
            cov.caveat = (cov.caveat + " " + c.reason).strip()
    return kept


def _finish(cannot: list[Cannot], text: str, age: bool, removed: list[str]) -> list[Cannot]:
    words = _words()
    for c in cannot:
        if not c.alternative or mentions_age(c.alternative):
            c.alternative = words["age_alternative"] if mentions_age(c.clause) else words["fallback_alternative"]
    if (age or removed) and not any(mentions_age(c.clause) for c in cannot):
        m = _age_pattern().search(text) or next((_age_pattern().search(r) for r in removed if mentions_age(r)), None)
        cannot.append(Cannot(m.group(0) if m else "age", words["age_reason"], words["age_alternative"]))
    for c in cannot:
        if mentions_age(c.clause):
            c.reason = words["age_reason"]
    return cannot


def context_for(repo, cam, night_ir: bool = True) -> CompileContext:
    """What the compiler is told about one camera. Society cameras switch to black and white IR at night."""
    from watchful.rules.zonestate import period_of
    names = {z.id: z.name for z in cam.zones}
    examples: dict[str, dict[str, list[str]]] = {}
    for _, zone_id, state, key in repo.list_state_examples(cam.id):
        if zone_id in names:
            periods = examples.setdefault(names[zone_id], {}).setdefault(state, [])
            if period_of(key) and period_of(key) not in periods:
                periods.append(period_of(key))
    return CompileContext(zones={z.name: z.kind for z in cam.zones}, state_examples=examples,
                          people={e.person_id: e.name for e in repo.list_enrollments()},
                          camera_flags={"night_ir": night_ir},
                          purpose=getattr(cam, "purpose", None))


def card(r: CompileResult) -> dict:
    """The compile result as the watch card shows it."""
    from dataclasses import asdict
    return {"watch": r.watch.model_dump() if r.watch else None, "problem": r.problem,
            "coverage": [asdict(c) for c in r.coverage], "setup": r.setup, "cannot": [asdict(c) for c in r.cannot]}


def compile_watch(text: str, ctx: CompileContext | list[str], llm: LLM,
                  people: dict[str, str] | None = None) -> CompileResult:
    if not isinstance(ctx, CompileContext):
        ctx = CompileContext(zones=list(ctx), people=people or {})
    words = _words()
    system = render("compile", catalog=catalog(), classes=COCO, vocabulary=sorted(vocabulary()), zones=ctx.zones,
                    lines=ctx.lines, state_examples=ctx.state_examples, people=ctx.people,
                    attributes=words["attributes"], camera_flags=ctx.camera_flags, purpose=ctx.purpose or "not set",
                    presets=describe())
    age = mentions_age(text)
    messages = [Msg("user", text)]
    last, data = "", {}
    for _ in range(ATTEMPTS):
        try:
            raw = llm.complete(system, messages, json_schema=SCHEMA)
        except (TimeoutError, AIError, AILimitReached):
            if last:
                break
            return CompileResult(None, error="The AI could not be reached. Try again in a moment.")
        try:
            data = parse_json(raw)
        except ValueError as e:
            last = _explain(e)
            continue  # a cut off or garbled reply: ask again from the start rather than feeding it back
        cannot = _cannot(data.get("cannot"))
        try:
            w, removed = (_strip_age(dict(data["watch"])) if data.get("watch") else (None, []))
            if w is None:
                problem = data.get("problem") or data.get("error")
                if not cannot and not removed:
                    cannot = [Cannot(text, str(problem or words["fallback_reason"]), words["fallback_alternative"])]
                cover = [c for c in _coverage(None, data.get("coverage"), ctx) if c.tier == "X"]
                return CompileResult(None, cover, [], _finish(cannot, text, age, removed))
            subject = w.setdefault("subject", {})
            subject["classes"] = resolve_classes(subject.get("classes") or ["person"])
            if any(c.get("signal") == "looks_like" for c in w.get("conditions", [])):
                subject.pop("looks_like", None)  # the condition already checks appearance
            watch = Watch.model_validate(w)
            who = watch.subject.who
            if who not in ("anyone", "unknown", "known") and who not in ctx.people:
                raise ValueError(f"who must be anyone, unknown, known or one of {sorted(ctx.people)}, not {who}")
            missing = _fix_names(watch, ctx)
            problems = validate(watch, set(ctx.names) | set(missing)) + _contradictions(watch)
            if problems:
                raise ValueError("; ".join(problems))
            if watch.output == "log" and not _asks_to_log(text):
                watch.output = "alert"  # only words that ask for a log make one
                watch.severity = "warning" if watch.severity == "info" else watch.severity
            if watch.output == "log":
                watch.severity, watch.repeat = "info", False  # one row each time it becomes true
            coverage = _coverage(watch, data.get("coverage"), ctx)
            cannot = _settle_overlaps(coverage, cannot)
            for cov in coverage:
                if cov.tier == "X" and not any(c.clause == cov.clause for c in cannot):
                    cannot.append(Cannot(cov.clause, cov.caveat or words["fallback_reason"], ""))
            return CompileResult(watch, coverage, _setup(watch, ctx, missing, data.get("setup")),
                                 _finish(cannot, text, age, removed))
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            last = _explain(e)
            messages = [Msg("user", text), Msg("assistant", raw),
                        Msg("user", f"That watch is not valid: {last}. Reply with fixed JSON.")]
    if last and data:
        cannot = _finish([Cannot(text, words["fallback_reason"], words["fallback_alternative"])], text, age, [])
        return CompileResult(None, [], [], cannot)
    return CompileResult(None, error=f"I couldn't turn that into a watch: {last}")
