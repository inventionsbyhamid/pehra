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
    c = Candidate("wat_gate", env.cams["gate"], 100.0, 1, "warning", {"reasons": ["gate open"]}, ["cam/kf/1.jpg"],
                  "fp", "Gate open late")
    v = verify(c, ctx(tmp_path), FakeLLM([step("raise_alert", summary="The gate is wide open with nobody near.",
                                              severity="Critical", reason="a child is walking towards the road")]))
    # the reason is read by people too, so it never calls anyone a child either (privacy fix round)
    assert (v.status, v.severity, v.reason) == ("alert", "critical", "Someone is walking towards the road")
    inc = inc_.after_check(env.a, c, v, "alt_9", now=at(0))
    assert inc.severity == "critical" and inc.reason.startswith("The gate is wide open")
    assert inc.agent_json["why"] == "Someone is walking towards the road"

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
        s.add(m.Attendance(workspace_id=env.a.workspace_id, staff_id=staff.id, day="2026-10-25", first_seen=at(1)))
    text = daily(env.a, day)
    assert text.splitlines()[0] == "Daily summary for Green Park, Sunday 25 October"
    assert "Alerts: 3 (1 critical, 1 warning, 1 note). 1 was a false alarm." in text
    assert "Open now: 3." in text and "Vehicles: 2 in, 1 out." in text and "Staff: 1 came in." in text
    assert "Cameras not live now: Pool (offline), Main gate (offline)." in text
    assert "False alarms by watch: A removed watch 1 of 1." in text
    assert "another society" not in text and "-" not in text and "—" not in text
    assert daily(env.a, date(2026, 10, 20)).splitlines()[1] == "Alerts: none."


def test_summary_is_queued_once_at_nine_society_time(env):
    from accounts import on_plan
    env.link(env.a, -1001)
    on_plan(env.a, env.a.workspace_id)  # the daily summary comes with the Society plan
    nine_ist = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
    assert queue_summaries(env.worker, nine_ist - timedelta(minutes=1)) == []
    assert queue_summaries(env.worker, nine_ist) == [env.a.workspace_id]  # Lake View has no group: nothing to send
    assert queue_summaries(env.worker, nine_ist + timedelta(minutes=5)) == []
    assert queue_summaries(env.worker, nine_ist + timedelta(hours=4)) == []
    env.run(nine_ist)
    sent = env.fake.of("sendMessage")[-1][2]
    assert sent["chat_id"] == "-1001" and sent["text"].startswith("Daily summary for Green Park, Sunday 25 October")


def test_the_camera_manager_files_live_candidates_as_incidents(tmp_path):
    """A critical candidate opens its incident with a private frame before the AI check; the verdict is edited in; a
    repeat of the open incident only adds to its count, with no second AI check."""
    import json
    import time

    import cv2
    import numpy as np

    from accounts import society_repo
    from watchful.components import make_blobs
    from watchful.config import Settings
    from watchful.evidence import EvidencePack
    from watchful.store.db import Camera
    from watchful.tuning import Tuning
    from watchful.workers import WorkerManager

    s = Settings(session_secret="y", data_dir=tmp_path, index_embeddings=False, scan_pool_at_start=False,
                 _env_file=None)
    repo = society_repo(s, "Green Park")
    pool = repo.add_camera(Camera(None, "Pool", "stream", "rtsp://pool")).id
    alert = json.dumps({"tool": "raise_alert", "args": {"summary": "A person is lying still at the deep end.",
                                                         "severity": "critical", "reason": "no movement"}, "why": "."})
    llm = FakeLLM([alert] * 5)
    manager = WorkerManager(s, Tuning(), repo, make_blobs(s), llm=llm)
    try:
        frame = cv2.imencode(".jpg", np.full((360, 640, 3), 120, np.uint8))[1].tobytes()
        now = time.time()

        def pack(fp: str, ts: float) -> EvidencePack:
            c = Candidate("wat_pool", pool, ts, 4, "critical", {"reasons": ["still in water"]}, [], fp,
                          "Someone still in the water")
            return EvidencePack(c, [(ts, frame)], [(ts, (300.0, 100.0, 60.0, 150.0))])

        manager._on_candidate(pack("fp1", now))
        early = inc_.list_incidents(repo)
        assert len(early) == 1 and early[0].reason == "Possible: Someone still in the water. Checking now."
        assert early[0].picture_key == f"{repo.workspace_id}/incidents/fp1.jpg" and early[0].picture_box
        end = time.time() + 20
        while "verdict" not in (inc_.get_incident(repo, early[0].id).agent_json or {}) and time.time() < end:
            time.sleep(0.1)
        checked = inc_.get_incident(repo, early[0].id)
        assert checked.reason == "A person is lying still at the deep end."
        assert checked.agent_json["why"] == "no movement"
        assert len(repo.list_alerts()) == 1 and len(llm.calls) == 1

        manager._on_candidate(pack("fp2", now + 30))
        assert inc_.get_incident(repo, early[0].id).count == 2 and len(llm.calls) == 1
        with repo.tx() as tx:
            jobs = tx.scalars(select(m.Job.kind).where(m.Job.kind.in_(("telegram.sync", "incident.escalate")))).all()
        assert sorted(set(jobs)) == ["incident.escalate", "telegram.sync"]
    finally:
        manager.shutdown()


# fix round 1


def live(env, fp: str, ts: float, cls: str | None = "person", camera: str = "pool") -> Candidate:
    return Candidate("wat_pool", env.cams[camera], at(ts).timestamp(), 1, "critical", {"reasons": ["still in water"]},
                     [], fp, "Someone still in the water", cls=cls)


def test_a_false_alarm_never_silences_a_later_real_alert(env):
    """C1: a guard closes the first one as a false alarm; for two hours new tracks keep triggering and the AI check
    finds each real. The closed incident takes none of them and the real ones are posted."""
    env.link(env.a, -1001)
    first = inc_.open_before_check(env.a, live(env, "fp0", 0))
    env.run(at(0))
    inc_.false_alarm(env.a, first.id, RAVI, at(10))
    env.run(at(10))
    real = Verdict("alert", "A child is face down in the deep end.", [], "critical", "not moving")
    for i in range(1, 25):
        ts = 300 * i
        c = live(env, f"fp{i}", ts)
        early = inc_.open_before_check(env.a, c)
        env.run(at(ts))
        inc_.after_check(env.a, c, real, f"alt_{i}", incident_id=early.id, now=at(ts + 5))
        env.run(at(ts + 5))
    closed = inc_.get_incident(env.a, first.id)
    assert (closed.status, closed.count, closed.reason) == ("false_alarm", 1, "Possible: Someone still in the water. "
                                                                              "Checking now.")
    open_now = inc_.list_incidents(env.a, ("open",))
    assert len(open_now) == 1 and open_now[0].reason == "A child is face down in the deep end."
    posts = env.fake.of("sendMessage")[1:]
    assert posts and "Possible: A child is face down in the deep end." in posts[0][2]["text"]
    assert posts[0][0] == at(305)  # held for the check after the tap, then posted at once when it was real


def test_a_false_alarm_quiets_only_its_own_trigger_for_a_fixed_window(env):
    env.link(env.a, -1001)
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "warning", "Pool in use late.", None, at(0),
                                 fingerprint="fpA")
    inc_.false_alarm(env.a, inc.id, RAVI, at(5))
    for ts in range(10, 300, 10):  # the same trigger keeps coming: it stays quiet, and the window does not move
        quiet = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "warning", "Pool in use late.", None,
                                       at(ts), fingerprint="fpA")
        assert quiet.id == inc.id
    after = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "warning", "Pool in use late.", None,
                                   at(5 + 301), fingerprint="fpA")
    assert after.id != inc.id and after.status == "open"
    other = open_or_touch_incident(env.b, env.cams["b_gate"], "wat_pool", "warning", "x", None, at(20),
                                   fingerprint="fpA")
    assert other.workspace_id == env.b.workspace_id
    closed = inc_.get_incident(env.a, inc.id)
    assert (closed.count, closed.last_at) == (1, at(0))
    assert inc_.false_alarm(env.a, inc.id, RAVI, at(400))[1] is False  # closed already
    gone = open_or_touch_incident(env.a, env.cams["gate"], "camera_offline", "warning", "Offline.", None, at(500))
    inc_.resolve(env.a, gone.id, at(510))
    assert inc_.false_alarm(env.a, gone.id, RAVI, at(520))[1] is False  # only open or acknowledged ones


def test_an_ai_dismissal_holds_later_triggers_until_checked(env):
    """I2: a guard sits by the pool after hours; each new track is dismissed by the AI check. Only the first one is
    posted (and cleared); later ones wait for the check, and a real one goes at once."""
    env.link(env.a, -1001)
    calm = Verdict("dismissed", "A guard is sitting on a chair by the pool.")
    for i in range(10):
        ts = 30 * i
        c = live(env, f"fp{i}", ts)
        early = inc_.open_before_check(env.a, c)
        env.run(at(ts))
        inc_.after_check(env.a, c, calm, incident_id=early.id, now=at(ts + 5))
        env.run(at(ts + 5))
    assert len(env.fake.of("sendMessage", "sendPhoto")) == 1
    assert len(env.fake.of("editMessageText", "editMessageCaption")) == 1
    assert "Cleared by the AI check" in env.fake.of("editMessageText")[0][2]["text"]
    assert len(inc_.list_incidents(env.a, ("resolved",))) == 10

    c = live(env, "fp_real", 400)
    early = inc_.open_before_check(env.a, c)
    env.run(at(400))
    assert len(env.fake.of("sendMessage", "sendPhoto")) == 1  # held
    inc_.after_check(env.a, c, Verdict("alert", "Someone slipped into the water.", [], "critical", "went under"),
                     incident_id=early.id, now=at(406))
    env.run(at(406))
    assert "Possible: Someone slipped into the water." in env.fake.of("sendMessage")[-1][2]["text"]
    env.run(at(400 + 120))
    assert "Still not acknowledged after 2 minutes" in env.fake.of("sendMessage")[-1][2]["text"]


def test_escalation_after_downtime_sends_one_reminder_with_the_real_minutes(env):
    """M-a: the worker was down for 30 minutes: one reminder saying so, not three at once."""
    env.link(env.a, -1001)
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", POOL_TEXT, None, at(0))
    for second in range(1800, 1806):
        env.run(at(second))
    for minute in range(31, 40):
        env.run(at(60 * minute))
    reminders = [c for c in env.fake.of("sendMessage") if "Still not" in c[2]["text"]]
    assert len(reminders) == 1 and "after 30 minutes" in reminders[0][2]["text"]


def test_the_edited_caption_still_says_possible(env):
    """M-h: the AI's words are a possibility; people decide."""
    env.link(env.a, -1001)
    c = live(env, "fp1", 0)
    early = inc_.open_before_check(env.a, c)
    env.run(at(0))
    inc_.after_check(env.a, c, Verdict("alert", "A person is floating face down."), incident_id=early.id, now=at(5))
    env.run(at(5))
    assert env.fake.of("editMessageText")[-1][2]["text"].splitlines()[1] == "Possible: A person is floating face down."


def test_incidents_are_deleted_after_each_societys_retention(env, monkeypatch):
    """M-g: a daily job in the job worker deletes incidents, their Telegram records and pictures after retention."""
    env.link(env.a, -1001)
    env.blobs.put("pool/frame.jpg", b"jpeg", "image/jpeg")
    old = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", POOL_TEXT, None, at(0),
                                 picture_key="pool/frame.jpg", picture_box=[1, 2, 3, 4])
    env.run(at(0))
    theirs = open_or_touch_incident(env.b, env.cams["b_gate"], "wat_b", "critical", "x", None, at(0))
    later = at(8 * 86400)
    recent = open_or_touch_incident(env.a, env.cams["gate"], "wat_gate", "critical", "y", None, later)
    monkeypatch.setattr(inc_, "retention_days", lambda settings, ws: 30 if ws.id == env.b.workspace_id else 7)
    assert env.worker.schedule_daily(later) and env.run(later)
    assert inc_.get_incident(env.a, old.id) is None and inc_.get_incident(env.a, recent.id) is not None
    assert inc_.get_incident(env.b, theirs.id) is not None  # Lake View keeps 30 days
    with env.a.tx() as s:
        assert s.scalar(select(m.TelegramMessage.id).where(m.TelegramMessage.incident_id == old.id)) is None
    with pytest.raises((OSError, KeyError)):
        env.blobs.get("pool/frame.jpg")


def test_after_a_false_alarm_the_next_real_alert_opens_and_is_posted(env):
    """C1 with the original calls only: the false alarm incident takes no more triggers, and the next moment the AI
    check finds real is its own incident, posted."""
    env.link(env.a, -1001)
    inc_.set_warnings(env.a, True)
    first = open_or_touch_incident(env.a, env.cams["gate"], "wat_gate", "warning", "The gate is open late.", None,
                                   at(0))
    env.run(at(0))
    inc_.false_alarm(env.a, first.id, RAVI, at(10))
    env.run(at(10))
    c = Candidate("wat_gate", env.cams["gate"], at(60).timestamp(), 2, "warning", {}, [], "fp2", "Gate open late")
    real = inc_.after_check(env.a, c, Verdict("alert", "The gate is wide open and a car is waiting."), "alt_2",
                            now=at(60))
    env.run(at(60))
    assert real.id != first.id and inc_.get_incident(env.a, first.id).count == 1
    assert "a car is waiting" in env.fake.of("sendMessage")[-1][2]["text"]


# fix round 2


def test_a_real_verdict_landing_on_a_held_incident_is_posted(env):
    """C1, the reviewer's sequence: A is posted; a guard taps false alarm on A while its check runs; a new track B
    opens held (the tap counts as a clearing); A's real verdict lands on B; B's own check then dismisses."""
    env.link(env.a, -1001)
    a = live(env, "fpA", 0)
    first = inc_.open_before_check(env.a, a)
    env.run(at(0))
    inc_.false_alarm(env.a, first.id, RAVI, at(3))
    env.run(at(3))
    b = live(env, "fpB", 6)
    held = inc_.open_before_check(env.a, b)
    assert held.agent_json.get("held")
    env.run(at(6))
    inc_.after_check(env.a, a, Verdict("alert", "A child is face down in the deep end.", [], "critical", "still"),
                     "alt_a", incident_id=first.id, now=at(8))
    env.run(at(8))
    assert "Possible: A child is face down in the deep end." in env.fake.of("sendMessage")[-1][2]["text"]
    inc_.after_check(env.a, b, Verdict("dismissed", "Only a towel on the water."), "alt_b", incident_id=held.id,
                     now=at(10))
    env.run(at(10))
    still = inc_.get_incident(env.a, held.id)
    assert still.status == "open" and still.reason == "A child is face down in the deep end."


def test_a_held_incident_whose_check_never_files_is_posted(env):
    """The reviewer's probe: held at 30 s, a trigger every 60 s for 30 minutes, and no verdict ever comes."""
    env.link(env.a, -1001)
    x = live(env, "fpX", 0)
    cleared = inc_.open_before_check(env.a, x)
    env.run(at(0))
    inc_.after_check(env.a, x, Verdict("dismissed", "A guard on a chair."), incident_id=cleared.id, now=at(5))
    env.run(at(5))
    held = inc_.open_before_check(env.a, live(env, "fpB", 30))
    assert held.agent_json.get("held")
    for second in range(30, 50):
        env.run(at(second))
    posted = [c for c in env.fake.of("sendMessage") if "Not checked by the AI yet." in c[2]["text"]]
    hold = env.worker.tuning.alerts.hold_max_s
    assert len(posted) == 1 and posted[0][0] == at(30 + hold)
    assert posted[0][2]["text"].splitlines()[1] == "Possible: Someone still in the water. Not checked by the AI yet."
    for minute in range(1, 31):
        open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "", None, at(30 + 60 * minute))
        env.run(at(30 + 60 * minute))
    assert inc_.get_incident(env.a, held.id).count == 31
    assert any("Still not acknowledged" in c[2]["text"] for c in env.fake.of("sendMessage"))


def test_a_folded_trigger_releases_a_held_incident_past_hold_max(env):
    """If the release job is lost too, the next trigger folded into a held incident older than hold_max_s posts it."""
    env.link(env.a, -1001)
    x = live(env, "fpX", 0)
    cleared = inc_.open_before_check(env.a, x)
    env.run(at(0))
    inc_.after_check(env.a, x, Verdict("dismissed", "A guard on a chair."), incident_id=cleared.id, now=at(5))
    env.run(at(5))
    held = inc_.open_before_check(env.a, live(env, "fpB", 30))
    with env.a.tx() as s:
        s.execute(m.Job.__table__.delete().where(m.Job.kind == "incident.release"))
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "", None, at(40))
    env.run(at(40))
    assert not any("Not checked" in c[2]["text"] for c in env.fake.of("sendMessage"))  # still within hold_max_s
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "", None, at(60))
    env.run(at(60))
    assert inc_.get_incident(env.a, held.id).agent_json.get("held") is False
    assert "Not checked by the AI yet." in env.fake.of("sendMessage")[-1][2]["text"]


def test_a_slow_upload_queue_does_not_delay_a_live_critical_check():
    """Live critical checks run before an upload's queued checks, on the camera manager's own pool."""
    import threading
    import time

    from watchful.alerts.checks import CheckPool
    from watchful.evidence import EvidencePack
    pool, order, gate = CheckPool(1), [], threading.Event()

    def check(pack, incident_id=None, live=False):
        if pack.candidate.fingerprint == "up0":
            gate.wait(5)
        order.append(pack.candidate.fingerprint)

    def pack(fp, severity="critical"):
        return EvidencePack(Candidate("w", "c", 0.0, 1, severity, {}, [], fp), [], [])
    futures = [pool.submit(check, pack(f"up{i}")) for i in range(5)]  # an upload: five checks, the first running
    time.sleep(0.1)
    futures.append(pool.submit(check, pack("live_info", "info"), None, True))
    futures.append(pool.submit(check, pack("live_critical"), "inc_1", True))
    gate.set()
    for f in futures:
        f.result(5)
    assert order[:3] == ["up0", "live_critical", "live_info"]
    pool.shutdown(wait=True)

    import inspect

    from watchful import workers
    assert "CheckPool(" in inspect.getsource(workers.WorkerManager.__init__)


def test_escalate_min_has_a_lower_bound():
    from pydantic import ValidationError

    from watchful.tuning import load_tuning
    with pytest.raises(ValidationError):
        load_tuning(overrides={"alerts": {"escalate_min": 0}})


def test_a_group_whose_post_was_given_up_still_gets_reminders(env):
    import httpx
    env.link(env.a, -1001)
    env.fake.replies[("sendMessage", "-1001")] = [
        httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: oops"})
        for _ in range(env.worker.tuning.alerts.chat_tries)]
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", POOL_TEXT, None, at(0))
    for second in range(0, 121, 15):
        env.run(at(second))
    reminders = [c for c in env.fake.of("sendMessage") if "Still not acknowledged" in c[2]["text"]]
    assert len(reminders) == 1 and reminders[0][0] == at(120) and "reply_parameters" not in reminders[0][2]


def test_a_held_critical_the_ai_cannot_judge_goes_out_at_once_as_unverified(env, tmp_path):
    """The verifier's cannot_judge ends unverified; a held critical incident goes out at once, not after hold_max_s."""
    from test_agent import ctx, step
    env.link(env.a, -1001)
    x = live(env, "fpX", 0)
    cleared = inc_.open_before_check(env.a, x)
    env.run(at(0))
    inc_.after_check(env.a, x, Verdict("dismissed", "A guard on a chair."), incident_id=cleared.id, now=at(5))
    env.run(at(5))
    c = live(env, "fpB", 30)
    held = inc_.open_before_check(env.a, c)
    assert held.agent_json.get("held")
    verdict = verify(c, ctx(tmp_path), FakeLLM([step("cannot_judge", reason="it needs an age")]))
    assert verdict.status == "unverified"
    inc_.after_check(env.a, c, verdict, "alt_b", incident_id=held.id, now=at(32))
    env.run(at(32))
    sent = env.fake.of("sendMessage")[-1]
    assert sent[0] == at(32) and "can't judge" in sent[2]["text"]
    assert inc_.get_incident(env.a, held.id).agent_json["held"] is False
