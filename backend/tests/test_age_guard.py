"""The age guard in layers (privacy fix round, Task 5 round 4): words and explicit age patterns, a meaning check on
every text a picture is compared with, the compile flag, the verifier's cannot_judge and its place names, counts that
are not ages, and names that are places only when the age word sits right before place words."""
import json

import pytest

from test_agent import cand, ctx as tool_ctx, step
from watchful.agent.compiler import CompileContext, Invalid, age_in_name, check_watch, compile_watch, mentions_age, \
    neutral_text
from watchful.agent.llm import FakeLLM
from watchful.agent.verifier import verify
from watchful.config import Settings
from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.zones import Zone


def words_only(**zones) -> CompileContext:
    """A context whose meaning check passes everything: the word layer on its own."""
    return CompileContext(zones=zones or {"water": "water", "pool deck": "area"}, meaning=lambda text: False)


def reply(watch, cannot=(), about_age=False):
    return json.dumps({"watch": watch, "coverage": [], "setup": [], "cannot": list(cannot), "about_age": about_age})


def looks(text, key="cond"):
    conds = [{"signal": "in_zone", "zones": ["water"]}]
    spec = {"name": "Someone at the pool", "subject": {"classes": ["person"]}, "conditions": conds,
            "verify": "Is someone there?"}
    if key == "cond":
        conds.append({"signal": "looks_like", "params": {"text": text, "not_text": "a person"}})
    elif key == "not_text":
        conds.append({"signal": "looks_like", "params": {"text": "a person", "not_text": text}})
    elif key == "zone":
        conds.append({"signal": "zone_looks_like", "zones": ["water"], "params": {"text": text, "not_text": "water"}})
    else:
        spec["subject"]["looks_like"] = text
    return spec


# ---- A: the words ----

AGE_TEXTS = ["a five-year-old", "seven year-old", "a ten yr old", "a person of about eight", "someone of about eight",
             "a child of about eight", "8 yrs", "bacha", "bache", "bachi", "bachon", "bacchon", "ladkon", "ladkiyon",
             "बच्चा", "लड़की", "\u0932\u095c\u0915\u0940", "preteen", "tween", "youth", "kindergartener",
             "class 5 student", "u18", "a person shorter than 4 feet", "a person under 120 cm", "a 3 ft tall person",
             "a nine-year-old", "a 5th grader", "das saal ki ladki", "under 12 yrs", "an adult"]


@pytest.mark.parametrize("words", AGE_TEXTS)
def test_a_age_texts_are_caught_by_the_words(words):
    assert mentions_age(words) and mentions_age(f"tell me when {words} is at the pool"), words


@pytest.mark.parametrize("key", ["cond", "not_text", "zone", "subject"])
@pytest.mark.parametrize("words", AGE_TEXTS)
def test_a_age_texts_never_reach_the_image_model(words, key):
    with pytest.raises(Invalid):
        check_watch(looks(words, key), "someone at the pool", words_only())


def test_a_devanagari_is_caught_in_either_unicode_form():
    one, two = "\u0932\u095c\u0915\u0940", "\u0932\u0921\u093c\u0915\u0940"  # ड़ as one code point; ड and nukta
    assert one != two and mentions_age(one) and mentions_age(two)
    assert neutral_text(f"एक {one} पूल में है") == "एक someone पूल में है"


# ---- A: the meaning check ----

MUST_REFUSE = ["a five-year-old", "seven year-old", "a ten yr old", "of about eight", "8 yrs", "bacha", "bache",
               "bachi", "bachon", "bacchon", "ladkon", "ladkiyon", "बच्चा", "लड़की", "preteen", "tween", "youth",
               "kindergartener", "class 5 student", "u18", "a person shorter than 4 feet", "a person under 120 cm"]
MUST_PASS = ["a delivery person with a bag", "a person in a red t shirt", "a guard in uniform", "a college student",
             "an old man with a stick", "a woman in a saree", "a group of 5 people", "a cab", "a dog"]
PRODUCT_LOOKS = ["a person carrying a ladder", "a person not carrying anything", "a person in everyday clothes",
                 "a delivery rider in an orange shirt with a big square bag", "smoke", "clear air", "an open gate",
                 "a closed gate", "a person not wearing a helmet", "a person wearing a helmet", "a person smoking",
                 "a swimmer", "someone swimming", "a person lying on the floor", "a maid", "a woman in a burqa"]
# the spellings, scripts and age groups the meaning check refuses with room to spare on both SigLIP 2 models; the
# number and height forms ("a five-year-old", "a person under 120 cm") and the bare "of about eight" it does not, and
# the words do
MEANING_REFUSES = ["bacha", "bache", "bachi", "bachon", "bacchon", "ladkon", "ladkiyon", "बच्चा", "लड़की", "preteen",
                   "tween", "youth", "kindergartener", "class 5 student"]


@pytest.fixture(scope="module", params=["siglip2_b32", "siglip2"])
def meaning(request):
    """Each SigLIP 2 model in turn, one text tower in memory at a time (the default one is the process's own)."""
    from watchful.agent.meaning import ChildMeaning
    from watchful.components import made_embedders, make_embedder
    from watchful.vision.embed import OnnxEmbedder
    settings = Settings(_env_file=None, data_key="k" * 32)
    for made in made_embedders():
        made.release_text()
    emb = make_embedder(settings) if request.param == settings.embedder else OnnxEmbedder(request.param, settings)
    yield ChildMeaning(emb, Tuning().signals.child_meaning_margin)
    emb.release_text()


def test_a_the_meaning_check_is_calibrated(meaning):
    refuse = {t: meaning.score(t)[0] for t in MUST_REFUSE}
    passing = {t: meaning.score(t)[0] for t in MUST_PASS + PRODUCT_LOOKS}
    assert all(not meaning(t) for t in MUST_PASS + PRODUCT_LOOKS), {t: s for t, s in passing.items() if s >= 0}
    assert all(meaning(t) for t in MEANING_REFUSES), {t: refuse[t] for t in MEANING_REFUSES}
    assert max(passing[t] for t in MUST_PASS) < meaning.margin - 0.02  # the brief's ordinary texts pass with room
    guard = CompileContext(meaning=meaning)
    for text in MUST_REFUSE:  # together the words and the meaning check refuse every one
        spec = looks("a person of about eight" if text == "of about eight" else text)
        with pytest.raises(Invalid):
            check_watch(spec, "someone at the pool", guard)


def test_a_the_meaning_check_catches_what_the_words_miss():
    """Paraphrases and spellings in no word list, on the default image model."""
    from watchful.agent.meaning import default_meaning
    assert Settings(_env_file=None, data_key="k" * 32).embedder == "siglip2_b32"
    check = default_meaning()
    from watchful.agent.compiler import looks_check
    for text in ["गुड़िया", "gudiya", "a person sucking a thumb", "a person with pigtails and a school bag"]:
        assert not mentions_age(text) and check(text), (text, check.score(text))
        with pytest.raises(Invalid):
            check_watch(looks(text), "someone at the pool", CompileContext(zones={"water": "water"}))
    assert not looks_check(CompileContext())("a college student")


def test_a_a_meaning_check_that_cannot_run_refuses():
    from watchful.agent.compiler import looks_check

    def broken(text):
        raise OSError("no text tower")
    assert looks_check(CompileContext(meaning=broken))("a person in a red t shirt")
    with pytest.raises(Invalid):
        check_watch(looks("a person in a red t shirt"), "x", CompileContext(zones={"water": "water"}, meaning=broken))


def test_a_the_compile_flag_makes_a_cannot():
    """The AI flags a request about children in words no list holds; it becomes the age Cannot."""
    from watchful.agent.compiler import _words
    watch = {"name": "Someone near the water", "subject": {"classes": ["person"]},
             "conditions": [{"signal": "in_zone", "zones": ["water"]}], "verify": "Is someone near the water?"}
    asked = "tell me when the little rascals go near the water"
    r = compile_watch(asked, words_only(), FakeLLM([reply(watch, about_age=True)]))
    assert not mentions_age(asked)
    assert r.cannot and r.cannot[-1].reason == _words()["age_reason"] and not mentions_age(r.cannot[-1].alternative)
    r = compile_watch("tell me when someone goes near the water", words_only(), FakeLLM([reply(watch)]))
    assert r.cannot == [] and r.watch is not None
    from watchful.agent.compiler import SCHEMA
    from watchful.agent.prompting import render
    assert "about_age" in SCHEMA["required"] and "about_age: true when" in render("compile")


# ---- B: only the cannot_judge tool means it needs an age ----

def test_b_cannot_judge_in_words_is_not_an_age_refusal(tmp_path):
    v = verify(cand("critical"), tool_ctx(tmp_path), FakeLLM([json.dumps({
        "tool": "raise_alert", "why": "I cannot judge the depth",
        "args": {"summary": "Someone is face down in the water and not moving.", "severity": "critical",
                 "reason": "face down"}})]))
    assert (v.status, v.summary, v.severity) == ("alert", "Someone is face down in the water and not moving.",
                                                 "critical")
    v = verify(cand(), tool_ctx(tmp_path / "b"), FakeLLM([step("dismiss", reason="A shadow; I cannot judge more.")]))
    assert v.status == "dismissed"
    v = verify(cand(), tool_ctx(tmp_path / "c"), FakeLLM([step("cannot_judge", reason="It needs an age.")]))
    assert v.status == "unverified" and "age" in v.summary


# ---- C: summaries keep the society's place names ----

def test_c_summaries_keep_place_names(tmp_path):
    c = tool_ctx(tmp_path)
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    c.repo.add_camera(Camera(None, "Play", "stream", None, [Zone("z1", "kids play area", square),
                                                            Zone("z2", "kids pool", square)]))
    c.repo.add_camera(Camera(None, "Block B", "stream", None, [Zone("z3", "girls toilet corridor", square)]))
    safe = "Car enters: the AI check agreed it is real."  # final fix A: a summary about age is replaced whole
    for said, reason, shown in (("Someone is in the kids play area after 10 PM.", "alone after hours", None),
                                ("Someone is still in the kids pool.", "still for long", None),
                                ("Someone is waiting in the girls toilet corridor.", "waiting", None),
                                ("Someone is in the kids play area after 10 PM.", "a little girl alone", safe),
                                ("A child is alone in the kids pool.", "alone", safe),
                                ("A girl of 6 is in the kids play area.", "alone", safe)):
        v = verify(cand(), c, FakeLLM([step("raise_alert", summary=said, severity="warning", reason=reason)]))
        assert v.summary == (shown or said) and not mentions_age(v.reason), (said, v.summary, v.reason)
        assert v.reason == ("" if shown else reason)
        c.images.clear()


# ---- D: counts are not ages ----

COUNTS = ["a group of 5 at the gate", "a group of 6 or more people", "a group of 4 men", "a crowd of 20 near the lobby",
          "a group of 3 guards", "a gathering of 15 or more", "a stay of 2 days", "one of 3 guards",
          "kept for 7 years", "a car parked for under 5 minutes", "under 10 seconds", "a group of 5 people"]


@pytest.mark.parametrize("words", COUNTS)
def test_d_counts_are_not_ages(words):
    assert not mentions_age(words) and neutral_text(words) == words[:1].upper() + words[1:]


def test_d_a_count_compiles_with_no_cannot():
    gate = words_only(**{"gate area": "area"})
    group = {"name": "A group of 5 at the gate", "subject": {"classes": ["person"]},
             "conditions": [{"signal": "count", "zones": ["gate area"], "op": ">=", "value": 5}],
             "verify": "Is a group of 5 or more standing at the gate?"}
    r = compile_watch("tell me when a group of 5 at the gate stays", gate, FakeLLM([reply(group)]))
    assert r.cannot == [] and r.watch.name == "A group of 5 at the gate"
    assert r.watch.verify == "Is a group of 5 or more standing at the gate?"


# ---- E: a name is a place only when the age word sits right before place words ----

@pytest.mark.parametrize("name", ["child alone at the gate", "small child near the pool", "minor at the gate",
                                  "7 year old zone", "girls only room", "kids only", "child or adult"])
def test_e_a_name_that_says_who_is_refused(name):
    assert age_in_name(name)
    assert mentions_age(f"someone in the {name} after 10 PM", [name])


@pytest.mark.parametrize("name", ["kids play area", "toddler pool", "girls toilet", "children's park",
                                  "Children’s park", "kids' play area", "the kids play area", "kids pool",
                                  "girls toilet corridor", "senior citizens park"])
def test_e_a_place_named_for_who_uses_it_passes(name):
    assert not age_in_name(name)
    assert not mentions_age(f"someone in the {name} after 10 PM", [name])
    assert mentions_age(f"a toddler in the {name}", [name])


def test_e_a_who_name_does_not_compile_into_a_place():
    ctx = words_only(**{"child alone at the gate": "area"})
    ai = reply({"name": "Someone in child alone at the gate", "subject": {"classes": ["person"]},
                "conditions": [{"signal": "in_zone", "zones": ["child alone at the gate"]}], "verify": "?"})
    r = compile_watch("someone in child alone at the gate", ctx, FakeLLM([ai]))
    assert r.cannot and any(mentions_age(c.clause) for c in r.cannot)


# ---- the four age agnostic watches compile and save, and presets and lookouts are checked too ----

AGNOSTIC = {
    "someone in the water after hours": ("pool", {
        "name": "Someone in the water after hours", "subject": {"classes": ["person"]}, "zones": ["water"],
        "conditions": [{"signal": "in_zone", "zones": ["water"]}], "time_window": {"start": "20:00", "end": "06:00"},
        "severity": "critical", "verify": "Is someone in the water?"}),
    "a group of 5 people at the gate": ("gate", {
        "name": "A group of 5 people at the gate", "subject": {"classes": ["person"]},
        "conditions": [{"signal": "count", "zones": ["gate area"], "op": ">=", "value": 5}],
        "severity": "warning", "verify": "Are 5 or more people at the gate?"}),
    "someone stays more than 10 minutes": ("gate", {
        "name": "Someone stays more than 10 minutes", "subject": {"classes": ["person"]},
        "conditions": [{"signal": "dwell_s", "zones": ["gate area"], "op": ">=", "value": 600}],
        "severity": "warning", "verify": "Has someone stayed at the gate for 10 minutes?"}),
    "college student at the gate": ("gate", {
        "name": "College student at the gate", "subject": {"classes": ["person"]},
        "conditions": [{"signal": "in_zone", "zones": ["gate area"]},
                       {"signal": "looks_like", "params": {"text": "a college student", "not_text": "a person"}}],
        "severity": "info", "verify": "Is a college student at the gate?"}),
}


def test_the_four_age_agnostic_watches_compile_and_save(tmp_path):
    from accounts import browser
    from test_api import StubManager
    from watchful.api import create_app
    app = create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, appearance_lookouts=True, _env_file=None),
                     llm=FakeLLM([reply(w) for _, w in AGNOSTIC.values()]), manager=StubManager())
    c = browser(app)
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    cams = {name: c.post("/api/cameras", json={"name": name, "kind": "stream", "url": f"rtsp://{name}"}).json()["id"]
            for name in ("pool", "gate")}
    c.patch(f"/api/cameras/{cams['pool']}", json={"zones": [{"id": "w", "name": "water", "kind": "water",
                                                             "polygon": square}]})
    c.patch(f"/api/cameras/{cams['gate']}", json={"zones": [{"id": "g", "name": "gate area", "polygon": square}]})
    for text, (cam, _) in AGNOSTIC.items():
        r = c.post(f"/api/cameras/{cams[cam]}/watches", json={"text": text}).json()
        assert r["saved"] is True and r["cannot"] == [] and r["problem"] is None, (text, r)
        again = c.post(f"/api/cameras/{cams[cam]}/watches", json={"text": text, "watch": r["watch"]})
        assert again.status_code == 200 and again.json()["cannot"] == [], (text, again.json())

    def preset(text):
        return c.post(f"/api/cameras/{cams['gate']}/watches", json={
            "preset": "looks_like_check", "fill": {"text": text, "not_text": "a person"}})
    assert preset("a person in a red t shirt").status_code == 200
    for text in ("a little girl", "a kindergartener", "gudiya"):
        assert preset(text).status_code == 400, text

    def lookout(description):
        return c.post("/api/lookouts", json={"camera_ids": [cams["gate"]], "description": description})
    assert lookout("a person in a red t shirt").status_code == 200
    before = len(c.get(f"/api/cameras/{cams['gate']}/watches").json())
    assert lookout("a ten yr old in a red frock").status_code == 400
    assert lookout("bachi in a red frock").status_code == 400
    assert len(c.get(f"/api/cameras/{cams['gate']}/watches").json()) == before
