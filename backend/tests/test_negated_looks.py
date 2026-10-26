"""A look watch phrased with not ("a person not wearing specs"). The image and text model reads "a person not wearing
glasses" almost like "a person wearing glasses", so on real people without glasses the pair scored about 0 and never
passed the margin (dev, 26 Oct: 2 of 14 portraits). The texts it compares are kept free of not, no and without: the
negated side becomes the plain subject ("a person"), which scored 13 of 14 the same way."""
import json

from watchful.agent.compiler import CompileContext, check_watch, compile_watch
from watchful.agent.llm import FakeLLM
from watchful.agent.prompting import render

DESK = CompileContext(purpose="other", meaning=lambda text: False)


def looks(text, not_text, classes=("person",)):
    return {"name": "Person not wearing specs", "subject": {"classes": list(classes)},
            "conditions": [{"signal": "looks_like", "params": {"text": text, "not_text": not_text, "hold_s": 2}}],
            "severity": "warning", "verify": "Is this person not wearing glasses?"}


def params(result):
    return result.watch.conditions[0].params


def test_a_watch_about_not_wearing_glasses_compares_with_the_plain_subject():
    # the AI's own reply on dev for "a person not wearing specs"
    ai = json.dumps({"watch": looks("a person not wearing glasses", "a person wearing glasses"),
                     "coverage": [{"clause": "a person not wearing specs", "signal": "looks_like", "tier": "B"}]})
    r = compile_watch("a person not wearing specs", DESK, FakeLLM([ai]))
    assert params(r)["text"] == "a person" and params(r)["not_text"] == "a person wearing glasses"
    assert params(r)["hold_s"] == 2
    assert [c.clause for c in r.coverage] == ["a person not wearing specs"]  # the card keeps the person's words
    assert r.watch.verify == "Is this person not wearing glasses?"


def test_either_side_and_every_negation_is_made_plain():
    for text, not_text, want in [
        ("a person carrying a ladder", "a person not carrying anything", ("a person carrying a ladder", "a person")),
        ("a rider without a helmet", "a rider wearing a helmet", ("a person", "a rider wearing a helmet")),
        ("a person with no mask", "a person wearing a mask", ("a person", "a person wearing a mask")),
        ("a person who isn't eating", "a person eating", ("a person", "a person eating")),
        ("a person eating", "a person sitting", ("a person eating", "a person sitting")),
    ]:
        r = check_watch(looks(text, not_text), "a look", DESK)
        assert (params(r)["text"], params(r)["not_text"]) == want, text


def test_the_plain_subject_follows_the_watch_class():
    r = check_watch(looks("a car not parked straight", "a car parked straight", ("car",)), "a car", DESK)
    assert params(r)["text"] == "a car"
    r = check_watch(looks("an umbrella not open", "an open umbrella", ("umbrella",)), "an umbrella", DESK)
    assert params(r)["text"] == "an umbrella"


def test_a_pair_that_would_become_the_same_words_is_left_as_written():
    r = check_watch(looks("a person not sitting", "a person not standing"), "a look", DESK)
    assert params(r)["text"] == "a person not sitting" and params(r)["not_text"] == "a person not standing"


def test_a_watch_saved_before_runs_with_plain_words_and_the_preset_keeps_its_name():
    from types import SimpleNamespace
    from watchful.rules.presets import preset
    from watchful.rules.watch import select_watches
    row = SimpleNamespace(id="wat_1", enabled=True, camera_id="cam_1", text="a person not wearing specs",
                          watch=looks("a person not wearing glasses", "a person wearing glasses"))
    (_, w), = select_watches([row], "cam_1", [])[0]
    assert w.conditions[0].params["text"] == "a person"
    w = preset("looks_like_check")
    assert w.conditions[0].params["text"] == "a person"
    assert w.name == "a person not wearing a helmet" and "not wearing a helmet" in w.verify


def test_the_compile_prompt_never_suggests_a_negated_look():
    prompt = render("compile")
    assert "not_text \"a person not carrying anything\"" not in prompt
    assert "never put not, no or without in text or not_text" in prompt
    assert "text \"a person\", not_text \"a person wearing glasses\"" in prompt

