import json

import numpy as np
import cv2

from watchful.agent.compiler import compile_watch
from watchful.agent.llm import FakeLLM
from watchful.agent.tools import ToolContext
from watchful.agent.verifier import verify
from watchful.notify import ApprovalNotifier
from watchful.rules.engine import Candidate
from watchful.rules.signals import REGISTRY
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import AlertRow, SqliteRepository


def watch_json(zone="parking lot", classes=("car",)):
    return json.dumps({"watch": {"name": "Car enters", "subject": {"classes": list(classes)},
                                 "conditions": [{"signal": "entered", "zones": [zone]}],
                                 "verify": "Is a car driving in?"}, "problem": None})


def test_compiler_happy_path():
    llm = FakeLLM([watch_json(classes=("vehicle",))])
    r = compile_watch("tell me when a car enters the parking lot", ["parking lot"], llm)
    assert r.problem is None and r.watch.conditions[0].signal == "entered"
    assert "car" in r.watch.subject.classes and "truck" in r.watch.subject.classes
    prompt = llm.calls[0][0]
    assert all(name in prompt for name in REGISTRY if not name.startswith("_")) and "parking lot" in prompt


def test_compiler_unknown_zone_is_a_setup_task():
    r = compile_watch("car enters the garage", ["parking lot"], FakeLLM([watch_json("garage")]))
    assert r.watch is not None and any("garage" in s for s in r.setup)


def test_compiler_survives_a_cut_off_reply_and_drops_doubled_appearance():
    doubled = json.dumps({"watch": {"name": "No helmet", "subject": {"classes": ["person"], "looks_like": "no helmet"},
                                    "conditions": [{"signal": "looks_like", "params": {"text": "a worker without a "
                                                    "helmet", "not_text": "a worker wearing a helmet"}}],
                                    "verify": "?"}, "problem": None})
    llm = FakeLLM(['{"watch": {"name": "x", "verify": "unterminated', "not json either", doubled])
    r = compile_watch("worker without a helmet", [], llm)
    assert r.watch is not None and r.watch.subject.looks_like is None
    assert all("unterminated" not in m.text for m in llm.calls[2][1])


def test_compiler_retries_after_invalid_json():
    r = compile_watch("car enters the parking lot", ["parking lot"], FakeLLM(["not json", watch_json()]))
    assert r.watch is not None


def ctx(tmp_path):
    img = np.full((240, 320, 3), 60, np.uint8)
    jpg = cv2.imencode(".jpg", img)[1].tobytes()
    blobs = LocalBlobStore(tmp_path)
    blobs.put("cam/kf/1.jpg", jpg, "image/jpeg")
    return ToolContext(SqliteRepository(tmp_path / "w.db"), blobs, "cam",
                       frames=lambda t0, t1, n: [jpg] * n, crops=lambda tid, ts: jpg,
                       poses=lambda tid, s: [{"ts": 1.0, "torso_angle": 80}],
                       boxes=lambda tid: [(0.0, (10.0, 10.0, 40.0, 90.0)), (1.0, (30.0, 20.0, 40.0, 90.0))])


def cand(severity="info", **evidence):
    return Candidate("w1", "cam", 100.0, 1, severity, {"reasons": ["came into parking lot"], **evidence},
                     ["cam/kf/1.jpg"], "fp", "Car enters", "Is a car driving in?")


def step(tool, **args):
    return json.dumps({"tool": tool, "args": args, "why": "because"})


def test_zoom_then_alert(tmp_path):
    v = verify(cand(), ctx(tmp_path), FakeLLM([step("crop_zoom", at_s=0),
                                                step("raise_alert", summary="A car drove in.")]))
    assert v.status == "alert" and v.summary == "A car drove in." and len(v.trace) == 2
    assert v.trace[0].tool == "crop_zoom" and v.trace[0].result


def test_enhance_then_dismiss(tmp_path):
    v = verify(cand(), ctx(tmp_path), FakeLLM([step("enhance", image="img1", kind="brighten"),
                                                step("dismiss", reason="It is a shadow.")]))
    assert v.status == "dismissed" and [t.tool for t in v.trace] == ["enhance", "dismiss"]


def test_critical_timeout_is_unverified(tmp_path):
    v = verify(cand("critical"), ctx(tmp_path), FakeLLM([TimeoutError("slow")]))
    assert v.status == "unverified" and "came into parking lot" in v.summary


def test_step_limit_falls_back(tmp_path):
    v = verify(cand("info"), ctx(tmp_path), FakeLLM([step("pose_history", seconds=6)] * 6), max_steps=3)
    assert v.status == "unverified" and len(v.trace) == 3 and "Not checked by the AI" in v.summary


def test_invalid_twice_falls_back(tmp_path):
    v = verify(cand("critical"), ctx(tmp_path), FakeLLM(["nope", "still nope"]))
    assert v.status == "unverified"


def test_ask_human(tmp_path):
    v = verify(cand("critical"), ctx(tmp_path), FakeLLM([step("ask_human", question="Call Sana's son?")]))
    assert v.status == "awaiting_human" and "Call" in v.summary


def test_face_match_skips_ai(tmp_path):
    llm = FakeLLM([])
    v = verify(cand(method="face"), ctx(tmp_path), llm)
    assert v.status == "measured" and llm.calls == []


class Sent:
    def __init__(self):
        self.messages = []

    def send(self, subject, body):
        self.messages.append(subject)


def test_approve_rate_limited():
    sent = Sent()
    n = ApprovalNotifier(sent, min_gap_s=600)
    alert = AlertRow("a1", "cam", "w1", 0.0, "critical", "awaiting_human", "Fell", None, None, [], None, "fp")
    assert n.approve(alert, now=1000.0) and not n.approve(alert, now=1300.0) and n.approve(alert, now=1700.0)
    assert len(sent.messages) == 2


def test_user_text_has_no_dashes():
    from watchful.agent.prompting import plain
    assert plain("Outside the left-walkway zone — never crossed – ok") == "Outside the left walkway zone, never crossed, ok"
    assert plain("At 9-10 am") == "At 9 to 10 am"


def test_parse_json_takes_the_first_of_several_objects():
    from watchful.agent.prompting import parse_json
    raw = 'Sure. {"tool": "people", "args": {}} then {"tool": "answer", "args": {"text": "x"}}'
    assert parse_json(raw)["tool"] == "people"


def test_any_ai_failure_falls_back(tmp_path):
    import httpx
    for error in (httpx.ConnectError("refused"), RuntimeError("botocore said no")):
        v = verify(cand("critical"), ctx(tmp_path), FakeLLM([error]))
        assert v.status == "unverified"
    v = verify(cand("critical"), ctx(tmp_path), FakeLLM([json.dumps({"tool": "crop_zoom", "args": "now", "why": "."}),
                                                          step("raise_alert", summary="Real.")]))
    assert v.status == "alert"
