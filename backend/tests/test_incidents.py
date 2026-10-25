"""Incidents group repeat triggers, go to Telegram by severity, escalate while nobody acts, and are summed up daily."""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from telegram_fakes import T0, make_env
from watchful.agent.llm import FakeLLM
from watchful.agent.verifier import Verdict, verify
from watchful.alerts import incidents as inc_
from watchful.alerts.incidents import Actor, open_or_touch_incident
from watchful.alerts.summary import daily, queue_summaries
from watchful.db import models as m
from watchful.rules.engine import Candidate

POOL_TEXT = "Someone is in the pool after hours."
RAVI = Actor("telegram:42", "Ravi", "telegram")


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_repeats_group_into_one_incident(env):
    """Review Focus 3: someone stays in the pool after hours and the watch triggers every second for 5 minutes."""
    env.link(env.a, -1001)
    key = env.picture()
    for i in range(300):
        open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", POOL_TEXT, key, at(i))
        env.run(at(i))
    for i in range(300, 330):
        env.run(at(i))

    incidents = inc_.list_incidents(env.a)
    assert len(incidents) == 1 and incidents[0].count == 300 and incidents[0].status == "open"
    photos = env.fake.of("sendPhoto")
    assert len(photos) == 1 and photos[0][0] == at(0)  # sent at once, with the still
    photo_id = 101
    reminders = env.fake.of("sendMessage")
    # nobody acknowledged: posted again after 2 and 4 minutes (a second later when an edit just went), as replies
    assert [round((c[0] - T0).total_seconds() / 60) for c in reminders] == [2, 4]
    assert all(0 <= (c[0] - T0).total_seconds() % 120 <= 2 for c in reminders)
    assert all(c[2]["reply_parameters"]["message_id"] == photo_id for c in reminders)
    edits = env.fake.of("editMessageCaption")
    assert 10 <= len(edits) <= 31 and "Seen 300 times" in edits[-1][2]["caption"]
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(edits, edits[1:])]
    assert min(gaps) >= env.worker.tuning.alerts.edit_every_s
    times = sorted(c[0] for c in env.fake.calls)
    assert max(sum(1 for u in times if t <= u < t + timedelta(minutes=1)) for t in times) <= 20


def test_escalation_reposts_unacknowledged_critical(env):
    env.link(env.a, -1001)
    pool = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", POOL_TEXT, None, at(0))
    gate = open_or_touch_incident(env.a, env.cams["gate"], "wat_fire", "critical", "Smoke near the gate.", None,
                                  at(0))
    env.run(at(0))
    env.run(at(1))
    # no picture: each goes as text, the second a second later (about one message a second in a chat)
    assert [c[0] for c in env.fake.of("sendMessage")] == [at(0), at(1)]
    inc_.acknowledge(env.a, gate.id, RAVI, at(60))
    for minute in range(1, 12):
        env.run(at(60 * minute))

    reminders = [c for c in env.fake.of("sendMessage")[2:]]
    assert [c[0] for c in reminders] == [at(120), at(240), at(360)]  # Tuning escalate_min 2, escalate_times 3
    assert all("Still not acknowledged" in c[2]["text"] and "Pool" in c[2]["text"] for c in reminders)
    assert reminders[0][2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"ack:{pool.id}"
    with env.a.tx() as s:
        rows = s.scalars(select(m.TelegramMessage).where(m.TelegramMessage.incident_id == gate.id)).all()
        assert [r.kind for r in rows] == ["incident"]  # acknowledged: never posted again

    inc_.acknowledge(env.a, pool.id, Actor("usr_1", "Meera"), at(700))
    for second in range(700, 705):
        env.run(at(second))
    edited = env.fake.of("editMessageText")
    pool_edits = [c for c in edited if c[0] >= at(700)]
    # its own message and the three reminders lose their Acknowledge button, one a second
    assert [c[0] for c in pool_edits] == [at(700), at(701), at(702), at(703)]
    assert all("Acknowledged by Meera" in c[2]["text"] for c in pool_edits)
    assert all(not any(b.get("callback_data", "").startswith("ack:") for row in c[2]["reply_markup"]["inline_keyboard"]
                       for b in row) for c in pool_edits)


def test_critical_sends_before_the_check_and_the_verdict_is_edited_in(env):
    env.link(env.a, -1001)
    c = Candidate("wat_pool", env.cams["pool"], at(0).timestamp(), 3, "critical", {"reasons": ["still in water"]},
                  [env.picture()], "fp1", "Someone still in the water")
    early = open_or_touch_incident(env.a, c.camera_id, c.watch_id, "critical", inc_.checking_text(c.watch_name),
                                   c.keyframe_keys[0], c.ts)
    env.run(at(0))
    first = env.fake.of("sendPhoto")[0][2]
    assert "Possible: Someone still in the water. Checking now." in first["caption"]
    assert first["caption"].startswith("Critical, Pool, 10:00 PM")

    verdict = Verdict("alert", "A person is floating face down near the deep end.", [], "critical",
                      "no movement for 20 seconds")
    inc_.after_check(env.a, c, verdict, "alt_1", incident_id=early.id, now=at(8))
    env.run(at(8))
    edit = env.fake.of("editMessageCaption")[-1][2]
    assert "floating face down" in edit["caption"] and "Graded critical: no movement for 20 seconds" in edit["caption"]
    assert "Checking now" not in edit["caption"]

    calm = open_or_touch_incident(env.a, env.cams["gate"], "wat_smoke", "critical", "Possible smoke.", None, at(20))
    env.run(at(20))
    inc_.after_check(env.a, c, Verdict("dismissed", "It is steam from the kitchen vent."), incident_id=calm.id,
                     now=at(25))
    env.run(at(25))
    assert inc_.get_incident(env.a, calm.id).status == "resolved"
    assert "Cleared by the AI check" in env.fake.of("editMessageText")[-1][2]["text"]
    for minute in range(1, 6):
        env.run(at(20 + 60 * minute))
    assert not any("steam" in c[2].get("text", "") and "Still" in c[2].get("text", "")
                   for c in env.fake.of("sendMessage"))  # a cleared incident is not escalated


def test_warnings_go_to_telegram_only_when_turned_on_and_info_never(env):
    env.link(env.a, -1001)
    open_or_touch_incident(env.a, env.cams["gate"], "wat_gate", "warning", "The gate is open late.", None, at(0))
    open_or_touch_incident(env.a, env.cams["gate"], "wat_cab", "info", "A cab came in.", None, at(0))
    env.run(at(0))
    assert env.fake.calls == []
    inc_.set_warnings(env.a, True)
    open_or_touch_incident(env.a, env.cams["pool"], "wat_late", "warning", "The pool is in use after hours.", None,
                           at(30))
    open_or_touch_incident(env.a, env.cams["gate"], "wat_cab", "info", "Another cab.", None, at(30))
    env.run(at(30))
    sent = env.fake.of("sendMessage", "sendPhoto")
    assert len(sent) == 1 and sent[0][2]["text"].startswith("Warning, Pool, 10:00 PM")
    for minute in range(1, 8):
        env.run(at(30 + 60 * minute))
    assert len(env.fake.of("sendMessage", "sendPhoto")) == 1  # warnings are never escalated


def test_the_agent_grades_severity_with_a_reason(tmp_path, env):
    from test_agent import ctx, step
    c = Candidate("wat_gate", "cam", 100.0, 1, "warning", {"reasons": ["gate open"]}, ["cam/kf/1.jpg"], "fp",
                  "Gate open late")
    v = verify(c, ctx(tmp_path), FakeLLM([step("raise_alert", summary="The gate is wide open with nobody near.",
                                              severity="Critical", reason="a child is walking towards the road")]))
    assert (v.status, v.severity, v.reason) == ("alert", "critical", "a child is walking towards the road")
    inc = inc_.after_check(env.a, c, v, "alt_9", now=at(0))
    assert inc.severity == "critical" and inc.reason.startswith("The gate is wide open")
    assert inc.agent_json["why"] == "a child is walking towards the road"

    plain = verify(c, ctx(tmp_path), FakeLLM([step("raise_alert", summary="Open.", severity="loud")]))
    assert plain.severity is None
    assert inc_.graded_severity("critical", "info") == "critical"  # only a dismissal clears a critical watch
    assert inc_.graded_severity("warning", "info") == "info" and inc_.graded_severity("warning", None) == "warning"


def test_grouping_window_and_scoping(env):
    first = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "warning", "One.", None, at(0))
    again = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Two.", None, at(300))
    assert again.id == first.id and again.count == 2 and again.severity == "critical" and again.reason == "One."
    lower = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "info", "Three.", None, at(400))
    assert lower.severity == "critical"
    later = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "warning", "Later.", None, at(400 + 601))
    assert later.id != first.id
    other_watch = open_or_touch_incident(env.a, env.cams["pool"], "wat_other", "warning", "x", None, at(401))
    health = open_or_touch_incident(env.a, env.cams["pool"], "camera_offline", "warning", "Offline.", None, at(401))
    assert len({first.id, other_watch.id, health.id}) == 3
    assert inc_.get_incident(env.b, first.id) is None
    assert inc_.acknowledge(env.b, first.id, RAVI, at(500)) == (None, False)
    assert inc_.get_incident(env.a, first.id).status == "open"
    assert inc_.active_incident(env.a, env.cams["pool"], "camera_offline", at(402)).id == health.id
    inc_.resolve(env.a, health.id, at(403), "Camera back online")
    assert inc_.active_incident(env.a, env.cams["pool"], "camera_offline", at(404)) is None
    with pytest.raises(ValueError):
        open_or_touch_incident(env.a, None, None, "loud", "x")


def test_daily_summary_counts_the_day(env):
    day = date(2026, 10, 25)
    pool = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "x", None, at(0))
    open_or_touch_incident(env.a, env.cams["gate"], "wat_gate", "warning", "y", None, at(10))
    open_or_touch_incident(env.a, env.cams["gate"], "wat_cab", "info", "z", None, at(20))
    inc_.false_alarm(env.a, pool.id, RAVI, at(30))
    open_or_touch_incident(env.a, env.cams["gate"], "wat_gate", "warning", "other day", None, at(86400))
    open_or_touch_incident(env.b, env.cams["b_gate"], "wat_b", "critical", "another society", None, at(0))
    with env.a.tx() as s:
        for direction in ("in", "in", "out"):
            s.add(m.VehiclePassage(workspace_id=env.a.workspace_id, camera_id=env.cams["gate"], at=at(5),
                                   direction=direction))
        staff = m.Staff(workspace_id=env.a.workspace_id, name="Sunita")
        s.add(staff)
        s.flush()
        s.add(m.Attendance(workspace_id=env.a.workspace_id, staff_id=staff.id, day="2026-10-25", first_in=at(1)))
    text = daily(env.a, day)
    assert text.splitlines()[0] == "Daily summary for Green Park, Sunday 25 October"
    assert "Alerts: 3 (1 critical, 1 warning, 1 note). 1 was a false alarm." in text
    assert "Open now: 3." in text and "Vehicles: 2 in, 1 out." in text and "Staff: 1 came in." in text
    assert "Cameras not live now: Pool (offline), Main gate (offline)." in text
    assert "False alarms by watch: A removed watch 1 of 1." in text
    assert "another society" not in text and "-" not in text and "—" not in text
    assert daily(env.a, date(2026, 10, 20)).splitlines()[1] == "Alerts: none."


def test_summary_is_queued_once_at_nine_society_time(env):
    env.link(env.a, -1001)
    nine_ist = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
    assert queue_summaries(env.worker, nine_ist - timedelta(minutes=1)) == []
    assert queue_summaries(env.worker, nine_ist) == [env.a.workspace_id]  # Lake View has no group: nothing to send
    assert queue_summaries(env.worker, nine_ist + timedelta(minutes=5)) == []
    assert queue_summaries(env.worker, nine_ist + timedelta(hours=4)) == []
    env.run(nine_ist)
    sent = env.fake.of("sendMessage")[-1][2]
    assert sent["chat_id"] == "-1001" and sent["text"].startswith("Daily summary for Green Park, Sunday 25 October")
