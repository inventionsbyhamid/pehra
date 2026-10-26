"""Final fix pass B: watches and claims that looked like they worked and did not, as tests."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from watchful.agent.compiler import CompileContext, Invalid, check_watch, compile_watch
from watchful.agent.llm import FakeLLM
from watchful.rules.engine import RuleEngine, validate
from watchful.rules.presets import preset, preset_names
from watchful.rules.watch import Condition, Subject, Watch, select_watches
from watchful.store.db import WatchRow
from watchful.tuning import Tuning
from watchful.vision.reason import Tick

TZ = "Asia/Kolkata"


def at(hh, mm=0, ss=0, day=23, tz=TZ):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=ZoneInfo(tz)).timestamp()


def W(*conds, classes=("person",), **kw):
    return Watch(name="w", subject=Subject(classes=list(classes)), conditions=list(conds), verify="?",
                 cooldown_s=kw.pop("cooldown_s", 1), **kw)


def reply(watch, setup=()):
    return json.dumps({"watch": watch, "coverage": [], "setup": list(setup), "cannot": [], "about_age": False})


@pytest.fixture
def app(tmp_path):
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings
    return create_app(Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None), llm=FakeLLM([]),
                      manager=StubManager())


# I2: a per signal contract, so a watch that could never fire is never saved or run

KINDS = {"gate": "area", "main": "line"}
NEVER = {
    "zone_state compared as a number": Condition(signal="zone_state", zones=["gate"], value="open"),
    "zone_state without a zone": Condition(signal="zone_state", op="is", value="open"),
    "posture compared as a number": Condition(signal="posture", value="fallen"),
    "camera_health compared as a number": Condition(signal="camera_health", value="offline"),
    "attribute compared as a number": Condition(signal="attribute", params={"name": "plate_colour"}, value="yellow"),
    "attribute without a name": Condition(signal="attribute", op="is", value="yellow"),
    "a list without in": Condition(signal="camera_health", op="is", value=["offline", "covered"]),
    "a posture that is never given": Condition(signal="posture", op="is", value="lying"),
    "a camera state that is never given": Condition(signal="camera_health", op="in", value=["dark", "offline"]),
    "looks_like without text": Condition(signal="looks_like", params={"not_text": "a person"}),
    "zone_looks_like without text": Condition(signal="zone_looks_like", zones=["gate"], params={"hold_s": 5}),
    "words without a word": Condition(signal="zone_state", zones=["gate"], op="is", value=True),
    "a number with words": Condition(signal="dwell_s", zones=["gate"], op=">=", value="long"),
}


@pytest.mark.parametrize("name", list(NEVER))
def test_a_watch_that_can_never_fire_is_refused(name):
    w = W(NEVER[name], classes=("person", "car"))
    assert validate(w, KINDS), name
    with pytest.raises(ValueError):
        RuleEngine([("w", w)], [], Tuning(), TZ)


@pytest.mark.parametrize("name", list(NEVER))
def test_a_stored_watch_that_can_never_fire_does_not_run_and_says_why(name):
    w = W(NEVER[name], classes=("person", "car"))
    from watchful.vision.zones import Zone
    gate = Zone("g1", "gate", [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])
    ok, notes = select_watches([WatchRow("w1", "cam", "text", w.model_dump(), True)], "cam", [gate])
    assert ok == [] and notes and notes[0].startswith("w: ")


def test_watches_that_can_fire_pass_the_contract():
    good = [Condition(signal="zone_state", zones=["gate"], op="is", value="open"),
            Condition(signal="zone_state", zones=["gate"], op="!=", value="closed"),
            Condition(signal="posture", op="is", value="fallen"),
            Condition(signal="posture", op="in", value="falling, fallen"),
            Condition(signal="camera_health", op="in", value=["offline", "covered"]),
            Condition(signal="attribute", params={"name": "plate_colour"}, op="is", value="yellow"),
            Condition(signal="attribute", params={"name": "plate_text"}, op="contains", value="KA01"),
            Condition(signal="looks_like", params={"text": "a person with a ladder"}),
            Condition(signal="zone_looks_like", params={"text": "smoke"}),
            Condition(signal="dwell_s", zones=["gate"], op=">=", value=30),
            Condition(signal="count", zones=["gate"], op=">=", value="3"),
            Condition(signal="entered", zones=["gate"]),
            Condition(signal="crossed", params={"line": "main", "direction": "in"}, op="is", value=True)]
    for c in good:
        assert validate(W(c), KINDS) == [], c
    fill = {"zone": "gate", "entries": ["gate"], "climbs": ["gate"]}
    for name in preset_names():
        assert validate(preset(name, **fill), KINDS) == [], name


class _Words:
    """An image model where every picture looks like smoke."""

    def text(self, t):
        return np.array([1.0, 0.0], np.float32) if t == "smoke" else np.array([0.0, 1.0], np.float32)

    def image(self, crop):
        return np.array([1.0, 0.0], np.float32)


def test_zone_looks_like_without_a_zone_watches_the_whole_picture():
    """The real AI made "smoke anywhere on this camera" a zone_looks_like with no zone; that read False for ever."""
    w = W(Condition(signal="zone_looks_like", params={"text": "smoke", "not_text": "clear air"}), classes=())
    assert validate(w, {}) == []
    e = RuleEngine([("w", w)], [], Tuning(), TZ, embedder=_Words())
    picture = np.full((240, 320, 3), 120, np.uint8)
    out = e.on_tick("cam", Tick(at(12), (320, 240), False, [], []), at(12), picture)
    assert len(out) == 1 and "smoke" in out[0].evidence["reasons"][0]


def test_the_compiler_asks_the_ai_again_on_a_contract_error():
    bad = reply({"name": "Smoke", "subject": {"classes": ["person"]}, "verify": "?",
                 "conditions": [{"signal": "zone_state", "value": "smoky"}]})
    good = reply({"name": "Smoke", "subject": {"classes": ["person"]}, "verify": "Is there smoke?",
                  "conditions": [{"signal": "zone_looks_like", "params": {"text": "smoke", "not_text": "clear air"}}]})
    llm = FakeLLM([bad, good])
    r = compile_watch("smoke anywhere on this camera", CompileContext(meaning=lambda text: False), llm)
    assert r.watch is not None and r.watch.conditions[0].signal == "zone_looks_like"
    retry = llm.calls[1][1][-1].text
    assert "zone_state" in retry and "needs a zone" in retry


def test_a_client_save_of_a_watch_that_can_never_fire_is_refused_in_plain_words():
    spec = W(Condition(signal="posture", value="fallen")).model_dump()
    with pytest.raises(Invalid) as e:
        check_watch(spec, "someone falls", CompileContext())
    assert "posture" in str(e.value) and "is, !=, contains or in" in str(e.value)
