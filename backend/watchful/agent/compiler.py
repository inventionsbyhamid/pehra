"""Plain words to a watch. The AI composes the generic building blocks; code then checks every part: the
catalog, spellings of zones and lines, setup tasks, coverage tiers and the privacy rule (no age, no children),
so nothing the catalog cannot express is saved as a watch that silently never fires."""
import copy
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from pydantic import ValidationError

from watchful.agent.llm import LLM, AIError, AILimitReached, Msg
from watchful.agent.meaning import default_meaning
from watchful.agent.prompting import parse_json, render
from watchful.rules.engine import STAY_PROBLEM, validate
from watchful.rules.presets import describe
from watchful.rules.signals import REGISTRY, TIERS, catalog, worse
from watchful.rules.vocab import DATA, resolve_classes, vocabulary
from watchful.rules.watch import Watch
from watchful.vision.detect import COCO

log = logging.getLogger(__name__)
ATTEMPTS = 3
SCHEMA = {"type": "object", "properties": {
    "watch": {"type": ["object", "null"]},
    "coverage": {"type": "array", "items": {"type": "object"}},
    "setup": {"type": "array", "items": {"type": "string"}},
    "cannot": {"type": "array", "items": {"type": "object"}},
    "about_age": {"type": "boolean"}},
    "required": ["watch", "coverage", "setup", "cannot", "about_age"]}


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
    camera_flags: dict = field(default_factory=dict)  # e.g. night_ir, attributes read on this camera
    purpose: str | None = None
    places: list[str] = field(default_factory=list)  # every zone and line name in the society, for the age check
    meaning: Callable[[str], bool] | None = None  # the meaning check on look texts; None is the process's own

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

    @property
    def place_names(self) -> list[str]:
        """Names that are only places, whatever words they hold (a kids play area)."""
        return self.names + list(self.places)


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


def _nfc(text) -> str:
    return unicodedata.normalize("NFC", str(text))


def _alternation(words) -> str:
    return "|".join(re.escape(_nfc(w)) for w in sorted(words, key=len, reverse=True))


@lru_cache
def _age_pattern() -> re.Pattern:
    """Age words and phrases (child, a minor, bachcha in Latin script or Devanagari, school uniform), explicit ages
    written with numbers or number words (7-year-old, seven year-old, 8 yrs) and heights standing in for age (shorter
    than 4 feet)."""
    return re.compile(r"\b(?:(?:a|an|the|some|any)\s+)?(" + "|".join(_words()["age_patterns"]) + "|"
                      + _alternation(_words()["age_terms"]) + r")(?!\w)", re.I)


@lru_cache
def _place_name() -> re.Pattern:
    """A name that is only a place: an age word directly before place words and nothing else (kids play area, toddler
    pool, girls toilet, children's park)."""
    return re.compile(r"(?:the\s+)?(?:" + _alternation(_words()["age_terms"]) + r")(?:['\u2019]s|s?['\u2019])?"
                      r"(?:\s+(?:" + _alternation(_words()["place_words"]) + r"))+", re.I)


def place_phrases(names) -> list[str]:
    """The drawn names that hold an age word and are still only a place: the age word sits directly before place
    words from data, with nothing else in the name (kids play area, toddler pool, children's park). "small child",
    "child alone at the gate", "minor at the gate" or "girls only room" say who, not where, so they are never places
    and can't hide an age."""
    out = []
    for name in names:
        name = _nfc(name or "").strip()
        if name and _age_pattern().search(name) and _place_name().fullmatch(re.sub(r"\s+", " ", name)):
            out.append(name)
    return out


def age_in_name(name: str) -> bool:
    """A zone or line name with an age word and no place word says who uses it, not where; it is refused."""
    return mentions_age(name) and not place_phrases([name])


def _mask(text: str, names=()) -> tuple[str, list[str]]:
    """Whole place phrases (a kids play area) and job titles (a delivery boy) swapped for markers, so their words
    are never read as an age; _unmask puts them back. Only whole phrases, never part of a word."""
    found: list[str] = []

    def keep(m: re.Match) -> str:
        found.append(m.group(0))
        return f"\x00{len(found) - 1}\x00"

    text = _nfc(text)
    for phrase in sorted({*place_phrases(names), *_words()["not_age_phrases"]}, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?!\w)", keep, text, flags=re.I)
    return text, found


def _unmask(text: str, found: list[str]) -> str:
    return re.sub("\x00(\\d+)\x00", lambda m: found[int(m.group(1))], text)


def mentions_age(text, names=()) -> bool:
    """Whether words are about a child or an age. names are drawn zones and lines, which are only places."""
    return bool(text) and _age_pattern().search(_mask(str(text), names)[0]) is not None


def _age_phrase(text: str, names=()) -> str | None:
    m = _age_pattern().search(_mask(text, names)[0])
    return m.group(0) if m else None


def neutral_text(text: str, names=()) -> str:
    """Words with every age or child word replaced by someone, for text people will read. names are the society's
    zone and line names, which are only places (the kids play area stays)."""
    return _neutral(text, names)


GOES = re.compile(r"(?:aged|under|below|shorter|smaller|less|lower|height)\b|u\s*-?\s*\d|.*(?:tall|high|in height)$",
                  re.I)  # age phrases that just go from a sentence; the rest become "someone"


def _neutral(text: str, names=()) -> str:
    masked, found = _mask(text, names)

    def swap(m: re.Match) -> str:  # "a child" becomes someone; "aged 7", "under 12 years" or "4 feet tall" just go
        return m.group(0)[:m.start(1) - m.start(0)] if GOES.match(m.group(1)) else "someone"

    out = re.sub(r"\s{2,}", " ", _age_pattern().sub(swap, masked))
    out = _unmask(re.sub(r"\s+([,.?!])", r"\1", out).strip(), found)
    return out[:1].upper() + out[1:]


def _explain(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
    return str(e).split("\n")[0]


def _clause(c: dict | object) -> str:
    params = c.get("params", {}) if isinstance(c, dict) else c.params
    signal = c.get("signal") if isinstance(c, dict) else c.signal
    return str(params.get("text") or signal)


LOOK_KEYS = ("text", "not_text")  # what looks_like and zone_looks_like compare pictures with


def looks_check(ctx: CompileContext) -> Callable[[str], bool]:
    """Whether a text a picture will be compared with is about age or children: the words, then the meaning check
    (agent/meaning.py), which loads the image model's text tower if it is not loaded. If the check can't run, the
    text is refused."""
    def about_age(text) -> bool:
        if not str(text or "").strip():
            return False
        if mentions_age(text):
            return True
        try:
            return bool((ctx.meaning or default_meaning())(str(text)))
        except Exception:
            log.warning("the meaning check could not run, so a look text was refused", exc_info=True)
            return True
    return about_age


def _strip_age(w: dict, names=(), looks: Callable[[str], bool] = mentions_age) -> tuple[dict | None, list[str]]:
    """Removes every part of a watch that is about age or children, and says what went. A watch whose subject was
    picked by age, or with nothing left, is dropped. Whole place phrases are only places, except in what a
    picture is compared with (`looks`: the words and the meaning check): "a child" is never a place."""
    removed, kept = [], []
    for c in w.get("conditions") or []:
        params = c.get("params") or {}
        texts = [str(params[k]) for k in LOOK_KEYS if params.get(k)]
        rest = {k: v for k, v in params.items() if k not in LOOK_KEYS}
        if any(looks(t) for t in texts) or mentions_age(json.dumps(rest, ensure_ascii=False), names) \
                or mentions_age(c.get("value"), names):
            removed.append(_clause(c))
        else:
            kept.append(c)
    subject = w.get("subject") or {}
    lost_subject = looks(subject.get("looks_like"))
    if lost_subject:
        removed.append(subject.pop("looks_like"))
    w["conditions"] = kept
    if lost_subject or (removed and not kept):
        return None, removed
    for key in ("name", "verify"):
        if mentions_age(w.get(key), names):
            removed.append(w[key])
            w[key] = _neutral(w[key], names)
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


def _ai_setup(item, names=()) -> bool:
    """Setup tasks the AI wrote are kept, except drawing and pictures: code works those out from the watch."""
    if not isinstance(item, str) or not item.strip() or mentions_age(item, names):
        return False
    low = item.strip().lower()
    return not (low.startswith("draw") or "picture" in low or "photo" in low)


def _setup(w: Watch, ctx: CompileContext, missing: list[str], given: list) -> list[str]:
    out = [s.strip() for s in given or [] if _ai_setup(s, ctx.place_names)]
    lines = {str(c.params.get("line") or (c.zones[0] if c.zones else "")) for c in w.conditions
             if c.signal == "crossed"}
    where = "on each camera it should run on" if w.scope == "site" else "on this camera"
    readers = set(ctx.camera_flags.get("attributes") or [])
    for c in w.conditions:
        name = str(c.params.get("name") or "")
        if c.signal == "attribute" and name and name not in readers:
            reader = _words()["readers"].get(name, name.replace("_", " ") + " reading")
            out.append(f"Turn on {reader} {'for this camera' if w.scope == 'camera' else where}.")
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
        if not isinstance(item, dict) or mentions_age(item.get("clause"), ctx.place_names) \
                or "output" in str(item.get("signal")):
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


def _settle_overlaps(coverage: list[Coverage], cannot: list[Cannot], names=()) -> list[Cannot]:
    """A part that is watched is not also a part that can't be: its limit becomes the coverage caveat."""
    watched = [(c.clause.strip().lower(), c) for c in coverage if c.tier != "X"]
    kept = []
    for c in cannot:
        clause = c.clause.strip().lower()
        cov = next((v for k, v in watched if clause and (clause in k or k in clause)), None)
        if cov is None or mentions_age(c.clause, names):
            kept.append(c)
        elif c.reason not in cov.caveat:
            cov.caveat = (cov.caveat + " " + c.reason).strip()
    return kept


def _finish(cannot: list[Cannot], text: str, age: bool, removed: list[str], names=()) -> list[Cannot]:
    words = _words()
    for c in cannot:
        if not c.alternative or mentions_age(c.alternative, names):
            c.alternative = words["age_alternative"] if mentions_age(c.clause, names) else words["fallback_alternative"]
    if (age or removed) and not any(mentions_age(c.clause, names) for c in cannot):
        phrase = _age_phrase(text, names) or next((p for r in removed if (p := _age_phrase(r, names))), None)
        cannot.append(Cannot(phrase or "age", words["age_reason"], words["age_alternative"]))
    for c in cannot:
        if mentions_age(c.clause, names):
            c.reason = words["age_reason"]
    return cannot


def context_for(repo, cam, night_ir: bool = True, attributes: list[str] = ()) -> CompileContext:
    """What the compiler is told about one camera. Society cameras switch to black and white IR at night.
    attributes are what readers attach on this camera (plate_text and plate_colour once plate reading is on)."""
    from watchful.rules.zonestate import period_of
    names = {z.id: z.name for z in cam.zones}
    examples: dict[str, dict[str, list[str]]] = {}
    for _, zone_id, state, key in repo.list_state_examples(cam.id):
        if zone_id in names:
            periods = examples.setdefault(names[zone_id], {}).setdefault(state, [])
            if period_of(key) and period_of(key) not in periods:
                periods.append(period_of(key))
    places = sorted({z.name for c in repo.list_cameras() for z in c.zones})
    return CompileContext(zones={z.name: z.kind for z in cam.zones}, state_examples=examples, places=places,
                          people={e.person_id: e.name for e in repo.list_enrollments()},
                          camera_flags={"night_ir": night_ir, "attributes": list(attributes)},
                          purpose=getattr(cam, "purpose", None))


def card(r: CompileResult) -> dict:
    """The compile result as the watch card shows it."""
    from dataclasses import asdict
    return {"watch": r.watch.model_dump() if r.watch else None, "problem": r.problem,
            "coverage": [asdict(c) for c in r.coverage], "setup": r.setup, "cannot": [asdict(c) for c in r.cannot]}


class Invalid(ValueError):
    """A watch that would compile but never fire, or break a rule; problems are plain sentences."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _kinds(ctx: CompileContext) -> dict[str, str]:
    return {**dict.fromkeys(ctx.zones, "area"), **dict.fromkeys(ctx.lines, "line")}


def _build(data: dict, text: str, ctx: CompileContext, from_ai: bool = True) -> CompileResult:
    """Every check code makes on a watch, the same for the AI's reply and for a watch a client sends to be saved.
    Raises Invalid (or a parse error) for a watch that must be fixed first."""
    words, names = _words(), ctx.place_names
    age = mentions_age(text, names) or (from_ai and data.get("about_age") is True)  # the AI's own flag
    cannot = _cannot(data.get("cannot"))
    w, removed = (_strip_age(dict(data["watch"]), names, looks_check(ctx)) if data.get("watch") else (None, []))
    if w is None:
        problem = data.get("problem") or data.get("error")
        if not cannot and not removed:
            cannot = [Cannot(text, str(problem or words["fallback_reason"]), words["fallback_alternative"])]
        cover = [c for c in _coverage(None, data.get("coverage"), ctx) if c.tier == "X"]
        return CompileResult(None, cover, [], _finish(cannot, text, age, removed, names))
    subject = w.setdefault("subject", {})
    subject["classes"] = resolve_classes(subject.get("classes") or ["person"])
    if any(c.get("signal") == "looks_like" for c in w.get("conditions", [])):
        subject.pop("looks_like", None)  # the condition already checks appearance
    watch = Watch.model_validate(w)
    who = watch.subject.who
    if who not in ("anyone", "unknown", "known") and who not in ctx.people:
        raise Invalid([f"who must be anyone, unknown, known or one of {sorted(ctx.people)}, not {who}"])
    missing = _fix_names(watch, ctx)
    problems = validate(watch, {**_kinds(ctx), **dict.fromkeys(missing)}) + _contradictions(watch)
    if problems:
        raise Invalid(problems)
    if from_ai and watch.output == "log" and not _asks_to_log(text):
        watch.output = "alert"  # only words that ask for a log make one; a person saving the card chose it
        watch.severity = "warning" if watch.severity == "info" else watch.severity
    if watch.output == "log":
        watch.severity, watch.repeat = "info", False  # one row each time it becomes true
    coverage = _coverage(watch, data.get("coverage"), ctx)
    cannot = _settle_overlaps(coverage, cannot, names)
    for cov in coverage:
        if cov.tier == "X" and not any(c.clause == cov.clause for c in cannot):
            cannot.append(Cannot(cov.clause, cov.caveat or words["fallback_reason"], ""))
    return CompileResult(watch, coverage, _setup(watch, ctx, missing, data.get("setup")),
                         _finish(cannot, text, age, removed, names))


def check_age(spec: dict, ctx: CompileContext) -> None:
    """Raises Invalid when any part of a watch about to be saved is about age or children, its look texts by words
    and by meaning: a client's card, a preset filled in, a lookout's description."""
    names = ctx.place_names
    _, removed = _strip_age(copy.deepcopy(spec), names, looks_check(ctx))
    rest = {k: v for k, v in spec.items() if k not in ("name", "verify", "conditions", "subject")}
    subject = {k: v for k, v in (spec.get("subject") or {}).items() if k not in ("looks_like", "same_as")}
    if removed or mentions_age(json.dumps([rest, subject], ensure_ascii=False), names):
        raise Invalid(["no watch may be about anyone's age"])


def check_watch(spec: dict, text: str, ctx: CompileContext, coverage=(), cannot=(), setup=()) -> CompileResult:
    """A watch a client sends to be saved (usually the one its card showed), checked again here exactly like the
    AI's: privacy, catalog, names and kinds, contradictions, output rules; coverage tiers and setup are worked out
    again rather than trusted (the card's own setup tasks pass the same filter as the AI's). Raises Invalid with
    plain problems."""
    check_age(spec, ctx)
    try:
        return _build({"watch": spec, "coverage": list(coverage), "cannot": list(cannot), "setup": list(setup)}, text,
                      ctx, from_ai=False)
    except Invalid:
        raise
    except (ValueError, KeyError, TypeError, ValidationError) as e:
        raise Invalid([_explain(e)]) from e


def compile_watch(text: str, ctx: CompileContext | list[str], llm: LLM,
                  people: dict[str, str] | None = None) -> CompileResult:
    if not isinstance(ctx, CompileContext):
        ctx = CompileContext(zones=list(ctx), people=people or {})
    words = _words()
    system = render("compile", catalog=catalog(), classes=COCO, vocabulary=sorted(vocabulary()), zones=ctx.zones,
                    lines=ctx.lines, state_examples=ctx.state_examples, people=ctx.people,
                    attributes=words["attributes"], camera_flags=ctx.camera_flags, purpose=ctx.purpose or "not set",
                    default_hours=words["default_hours"], presets=describe())
    mixed = ""  # the last line and area mix up, which becomes the reason if the AI never fixes it
    stay = ""  # the same for inside_s mixed with what the gate log cannot know
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
        try:
            return _build(data, text, ctx)
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            if isinstance(e, Invalid):
                mixed = next((p for p in e.problems if " not a line" in p or " not an area" in p), mixed)
                stay = next((p for p in e.problems if p.startswith(STAY_PROBLEM)), stay)
            last = _explain(e)
            messages = [Msg("user", text), Msg("assistant", raw),
                        Msg("user", f"That watch is not valid: {last}. Reply with fixed JSON.")]
    if last and data:
        reason = (mixed[:1].upper() + mixed[1:] + ".") if mixed else words["fallback_reason"]
        alternative = words["fallback_alternative"]
        if stay and not mixed:
            reason, alternative = words["stay_reason"], words["stay_alternative"]
        cannot = _finish([Cannot(text, reason, alternative)], text,
                         mentions_age(text, ctx.place_names) or data.get("about_age") is True, [], ctx.place_names)
        return CompileResult(None, [], [], cannot)
    return CompileResult(None, error=words["unreadable"])  # never the parser's own words
