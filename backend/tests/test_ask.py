import json
from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from watchful.agent.ask import AskContext, ask, people_tool
from watchful.agent.llm import FakeLLM
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import EventRow, SqliteRepository

TZ = "Asia/Kolkata"


def at(hh, mm):
    return datetime(2026, 10, 23, hh, mm, tzinfo=ZoneInfo(TZ)).timestamp()


def sig(color):
    img = np.zeros((40, 20, 3), np.uint8)
    img[:] = color
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h, h, 1.0, 0.0, cv2.NORM_L1)
    return h.flatten().tolist()


def ev(kind, tid, ts, zone=None, label=None, color=(40, 160, 40), key=None):
    return EventRow(None, "cam", ts, kind, tid, "person", zone, {}, key or f"cam/kf/{tid}.jpg", sig(color), None, label)


@pytest.fixture
def ctx(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    blobs = LocalBlobStore(tmp_path)
    for tid in (1, 2, 3):
        blobs.put(f"cam/kf/{tid}.jpg", cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes(), "image/jpeg")
    return AskContext(repo, blobs, "cam", TZ)


def seed_sana(repo):
    repo.add_events([ev("track_start", 1, at(9, 1), label="Sana"), ev("enter", 1, at(9, 2), "kitchen", "Sana"),
                     ev("exit", 1, at(9, 47), "kitchen", "Sana"), ev("track_end", 1, at(9, 48), label="Sana")])


def test_people_tool_reports_zone_visits(ctx):
    seed_sana(ctx.repo)
    text, _ = people_tool(ctx, since=at(0, 0), until=at(23, 0))
    assert "Sana" in text and "kitchen" in text and "09:02" in text and "09:47" in text


def step(tool, **args):
    return json.dumps({"tool": tool, "args": args, "why": "."})


def test_answer_keeps_only_grounded_items(ctx):
    seed_sana(ctx.repo)
    real = ctx.repo.query_events(kinds=["enter"])[0]
    llm = FakeLLM([step("people", since=at(0, 0), until=at(23, 0)),
                   step("answer", text="Sana came in at 09:02 and left at 09:47.", confidence="high",
                        items=[{"label": "Sana in the kitchen", "event_id": real.id, "start_ts": at(9, 2),
                                "end_ts": at(9, 47)},
                               {"label": "Made up", "event_id": "evt_nope", "start_ts": at(10, 0)}])])
    a = ask("when did Sana come into the kitchen and when did she leave", "cam", ctx, llm, now=at(12, 0))
    assert [i.label for i in a.items] == ["Sana in the kitchen"]
    assert a.items[0].thumbnail_url == "/api/blobs/cam/kf/1.jpg" and a.confidence == "high"
    assert [t.tool for t in a.trace] == ["people", "answer"]


def test_lookalikes_cap_confidence(ctx):
    ctx.repo.add_events([ev("track_start", 1, at(9, 0)), ev("enter", 1, at(9, 2), "kitchen"),
                         ev("track_start", 2, at(9, 1)), ev("enter", 2, at(9, 3), "kitchen"),
                         ev("track_end", 1, at(9, 30)), ev("track_end", 2, at(9, 31))])
    e1 = ctx.repo.query_events(kinds=["enter"])[0]
    llm = FakeLLM([step("people", since=at(0, 0), until=at(23, 0)),
                   step("answer", text="She came in at 09:02.", confidence="high",
                        items=[{"label": "maid", "event_id": e1.id, "start_ts": at(9, 2)}])])
    a = ask("when did the maid come into the kitchen", "cam", ctx, llm, now=at(12, 0))
    assert a.confidence == "low" and "look alike" in a.text


def test_prompt_has_the_full_date_and_a_schema(ctx):
    llm = FakeLLM([step("answer", text="Nothing.", items=[], confidence="high")])
    ask("anyone today?", "cam", ctx, llm, now=at(12, 0))
    system, _, _, schema = llm.calls[0]
    assert "2026-10-23" in system and str(int(at(12, 0))) in system and schema is not None


def test_lookalikes_are_listed_even_without_the_people_tool(ctx):
    ctx.repo.add_events([ev("track_start", 1, at(9, 0)), ev("enter", 1, at(9, 2), "kitchen"),
                         ev("exit", 1, at(9, 40), "kitchen"),
                         ev("track_start", 2, at(9, 1)), ev("enter", 2, at(9, 3), "kitchen"),
                         ev("track_end", 1, at(9, 41)), ev("track_end", 2, at(9, 31))])
    first = next(e for e in ctx.repo.query_events(kinds=["enter"]) if e.track_id == 1)
    llm = FakeLLM([step("answer", text="She came in at 09:02.", confidence="high",
                        items=[{"label": "maid", "event_id": first.id, "start_ts": 1.0, "end_ts": 2.0}])])
    a = ask("when did the maid come into the kitchen and leave", "cam", ctx, llm, now=at(12, 0))
    assert a.confidence == "low" and len(a.items) == 2
    assert a.items[0].start_ts == at(9, 2) and a.items[0].end_ts == at(9, 40)  # times from the events
