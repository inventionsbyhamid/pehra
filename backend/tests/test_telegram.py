"""Telegram: linking a group with a one time code, taps that count only from the incident's own society, a false
alarm that closes for everyone, stills with faces of people who are not enrolled blurred, and the bot's limits."""
import logging
from datetime import UTC, datetime, timedelta

import cv2
import httpx
import numpy as np
import pytest
from sqlalchemy import select

from telegram_fakes import T0, TOKEN, make_env
from watchful import components
from watchful.alerts import incidents as inc_
from watchful.alerts.crops import blurred_crop, frame_still
from watchful.alerts.incidents import open_or_touch_incident
from watchful.alerts.telegram import (TelegramBot, TelegramChannel, TelegramError, buttons, caption, connect_code,
                                      handle_update, linked_groups)
from watchful.config import Settings
from watchful.db import models as m
from watchful.notify import LogChannel
from watchful.rules.engine import Candidate
from watchful.store.db import Camera
from watchful.tuning import CropTuning
from watchful.vision.faces import FaceIndex
from watchful.vision.models import sample_path


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def at(seconds: float):
    return T0 + timedelta(seconds=seconds)


def connect(env, code: str, chat_id: int, title="Guards", now=T0):
    return handle_update(env.a, env.repo_for, {"message": {"chat": {"id": chat_id, "type": "supergroup",
                                                                    "title": title},
                                                           "from": {"id": 7, "first_name": "Asha"},
                                                           "text": f"/connect@SocietyBot {code}"}}, now)


def audit(env, action: str) -> list[m.Audit]:
    with env.a.tx() as s:
        return list(s.scalars(select(m.Audit).where(m.Audit.action == action).order_by(m.Audit.ts)))


def test_connect_with_code(env):
    code, expires = connect_code(env.a, env.a.workspace_id, T0)
    assert len(code) == 8 and expires == T0 + timedelta(minutes=30)
    with env.a.tx() as s:
        assert s.scalar(select(m.TelegramLink.code_hash)) != code  # only the hash is kept

    reply = connect(env, code.lower(), -1001, "Green Park guards", at(60))
    assert reply == {"method": "sendMessage", "chat_id": "-1001",
                     "text": "Connected to Green Park. Alerts will come to this group."}
    assert [(g.chat_id, g.title) for g in linked_groups(env.a, env.a.workspace_id)] == [("-1001", "Green Park guards")]
    assert audit(env, "telegram.connect")[0].workspace_id == env.a.workspace_id

    assert "not valid" in connect(env, code, -1002)["text"]  # used once
    assert linked_groups(env.a, env.a.workspace_id)[0].chat_id == "-1001"
    late, _ = connect_code(env.a, env.a.workspace_id, T0)
    assert "not valid" in connect(env, late, -1003, now=at(31 * 60))["text"]  # expired
    assert "not valid" in connect(env, "WRONG123", -1004)["text"]
    assert "followed by the code" in connect(env, "", -1005)["text"]

    theirs, _ = connect_code(env.a, env.b.workspace_id, T0)
    assert "another society" in connect(env, theirs, -1001)["text"]  # one group belongs to one society
    assert linked_groups(env.a, env.b.workspace_id) == []
    again, _ = connect_code(env.a, env.a.workspace_id, T0)
    assert connect(env, again, -1001)["text"] == "This group is already connected."

    removed = {"my_chat_member": {"chat": {"id": -1001}, "new_chat_member": {"status": "kicked"}}}
    assert handle_update(env.a, env.repo_for, removed, at(90)) is None
    assert linked_groups(env.a, env.a.workspace_id) == []


def posted(env, chat_id: int, now=T0) -> tuple[m.Incident, str]:
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, now,
                                 fingerprint="fp_pool")
    env.run(now)
    with env.a.tx() as s:
        msg = s.scalar(select(m.TelegramMessage).where(m.TelegramMessage.incident_id == inc.id,
                                                       m.TelegramMessage.chat_id == str(chat_id)))
    return inc, msg.message_id


def test_tap_from_unlinked_chat_is_refused(env):
    """Review Focus 4: a tap counts only from a chat linked to the incident's own society."""
    env.link(env.a, -1001)
    env.link(env.b, -2002, "Lake View guards")
    inc, message_id = posted(env, -1001)

    for chat in (-9999, -2002):  # a stranger's chat, and the other society's group
        answer = env.tap(chat, message_id, f"false:{inc.id}", now=at(5))
        assert answer["method"] == "answerCallbackQuery" and answer["show_alert"] is True
    assert "not connected" in env.tap(-9999, message_id, f"false:{inc.id}")["text"]
    assert "does not belong" in env.tap(-2002, message_id, f"ack:{inc.id}")["text"]
    assert "does not belong" in env.tap(-1001, 999, f"ack:{inc.id}")["text"]  # not the bot's message about it
    assert "not known" in env.tap(-1001, message_id, "delete:all")["text"]
    after = inc_.get_incident(env.a, inc.id)
    assert (after.status, after.ack_by) == ("open", None)
    assert audit(env, "incident.false_alarm") == [] and audit(env, "incident.acknowledge") == []

    assert env.tap(-1001, message_id, f"ack:{inc.id}", now=at(9))["text"] == "Acknowledged. Thank you."
    assert inc_.get_incident(env.a, inc.id).status == "acknowledged"
    assert env.tap(-1001, message_id, f"ack:{inc.id}", user=(43, "Kiran"))["text"] == \
        "Already acknowledged by Ravi."


def test_false_alarm_closes_for_everyone_and_is_labelled(env):
    """Review Focus 4: the society's two supervisors each have a group; a guard taps False alarm in one."""
    env.link(env.a, -1001, "Guards")
    env.link(env.a, -1002, "Committee")
    inc, message_id = posted(env, -1001)
    assert len(env.fake.of("sendMessage")) == 2

    assert env.tap(-1001, message_id, f"false:{inc.id}", now=at(20))["text"] == "Closed as a false alarm."
    env.run(at(20))
    closed = inc_.get_incident(env.a, inc.id)
    assert (closed.status, closed.ack_by, closed.ack_at, closed.closed_at) == ("false_alarm", "Ravi", at(20), at(20))
    edits = env.fake.of("editMessageText")
    assert sorted(c[2]["chat_id"] for c in edits) == ["-1001", "-1002"]  # closed in both groups
    for _, _, data in edits:
        assert "Closed as a false alarm by Ravi at 10:00 PM." in data["text"]
        assert not any("callback_data" in b for row in data["reply_markup"]["inline_keyboard"] for b in row)
    label = audit(env, "incident.false_alarm")[0]
    assert (label.actor, label.ts, label.data["name"], label.data["via"], label.data["watch"], label.data["chat"]) == \
        ("telegram:42", at(20), "Ravi", "telegram", "wat_pool", "-1001")
    assert inc_.precision_by_watch(env.a)["wat_pool"]["false_alarm"] == 1
    assert inc_.precision_by_watch(env.a)["wat_pool"]["precision"] == 0.0

    with env.a.tx() as s:
        committee = s.scalar(select(m.TelegramMessage.message_id).where(m.TelegramMessage.chat_id == "-1002"))
    assert env.tap(-1002, committee, f"ack:{inc.id}", user=(44, "Meera"))["text"] == "This alert is already closed."
    # the very trigger the guard closed stays quiet for a while, and the closed incident takes nothing more
    again = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None,
                                   at(60), fingerprint="fp_pool")
    for minute in range(1, 6):
        env.run(at(60 * minute))
    closed = inc_.get_incident(env.a, inc.id)
    assert again.id == inc.id and (closed.status, closed.count) == ("false_alarm", 1)
    assert len(env.fake.of("sendMessage", "sendPhoto")) == 2  # no reminders for a closed incident

    # but the AI check of a new trigger finding it real is never silenced by the earlier tap
    from watchful.agent.verifier import Verdict
    c = Candidate("wat_pool", env.cams["pool"], at(330).timestamp(), 9, "critical", {}, [], "fp_new", "Pool",
                  cls="person")
    real = inc_.after_check(env.a, c, Verdict("alert", "A child is face down in the deep end."), "alt_2", now=at(330))
    env.run(at(330))
    assert real.id != inc.id and inc_.get_incident(env.a, inc.id).reason == "Someone is in the pool."
    posts = env.fake.of("sendMessage")[2:]
    assert sorted(c[2]["chat_id"] for c in posts) == ["-1001", "-1002"]
    assert all("Possible: A child is face down in the deep end." in c[2]["text"] for c in posts)


def test_buttons_and_caption_link_to_the_clip_behind_sign_in(env):
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, T0)
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
    rows = buttons(inc, "https://society.example.com")
    assert rows == [[("Acknowledge", f"ack:{inc.id}"), ("False alarm", f"false:{inc.id}")],
                    [("Open the clip", f"https://society.example.com/incidents/{inc.id}")]]
    assert all(len(a.encode()) <= 64 for row in rows[:1] for _, a in row)  # Telegram's callback data limit
    text = caption(inc, "Pool", tz, "https://society.example.com")
    assert text == "Critical, Pool, 10:00 PM\nSomeone is in the pool.\nNot acknowledged yet."
    assert caption(inc, "Pool", tz, "http://localhost:8000").endswith(
        f"Clip: http://localhost:8000/incidents/{inc.id}")
    assert buttons(inc, "http://localhost:8000") == rows[:1]


def faces_index() -> FaceIndex:
    index = FaceIndex()
    index.enroll("stf_1", "Player", cv2.imread(str(sample_path("basketball1.png"))), consent=True)
    return index


def found_faces(image) -> list[tuple[int, int, int, int]]:
    probe = FaceIndex()
    h, w = image.shape[:2]
    probe.detector.setInputSize((w, h))
    _, faces = probe.detector.detect(image)
    return [tuple(int(v) for v in f[:4]) for f in ([] if faces is None else faces)]


def sharpness(image, box) -> float:
    x, y, w, h = box
    return float(cv2.Laplacian(cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def test_crop_blurs_non_enrolled_faces():
    player = cv2.imread(str(sample_path("basketball2.png")))
    stranger = cv2.imread(str(sample_path("messi5.jpg")))
    h = player.shape[0]
    stranger = cv2.resize(stranger, (round(stranger.shape[1] * h / stranger.shape[0]), h))
    frame = np.hstack([player, stranger])
    faces = found_faces(frame)
    mine = [f for f in faces if f[0] < player.shape[1]]
    theirs = [f for f in faces if f[0] >= player.shape[1]]
    assert mine and theirs

    whole = CropTuning(max_side=4000)
    out = cv2.imdecode(np.frombuffer(blurred_crop(frame, None, faces_index(), cfg=whole), np.uint8), cv2.IMREAD_COLOR)
    assert out.shape == frame.shape
    enrolled = max(mine, key=lambda f: f[2] * f[3])  # the enrolled player stays recognisable
    assert sharpness(out, enrolled) > 0.6 * sharpness(frame, enrolled)
    for box in theirs:
        assert sharpness(out, box) < 0.25 * sharpness(frame, box)

    nobody = FaceIndex()  # nobody enrolled: every face is blurred
    out = cv2.imdecode(np.frombuffer(blurred_crop(frame, None, nobody, cfg=whole), np.uint8), cv2.IMREAD_COLOR)
    assert all(sharpness(out, box) < 0.25 * sharpness(frame, box) for box in faces)

    turned = np.random.default_rng(1).integers(0, 255, (400, 300, 3), np.uint8)  # a person facing away: no face
    out = cv2.imdecode(np.frombuffer(blurred_crop(turned, None, nobody, heads=[(50, 20, 200, 360)]), np.uint8),
                       cv2.IMREAD_COLOR)
    assert sharpness(out, (60, 30, 180, 90)) < 0.1 * sharpness(turned, (60, 30, 180, 90))
    assert sharpness(out, (60, 250, 180, 100)) > 0.5 * sharpness(turned, (60, 250, 180, 100))

    jpeg, box = frame_still(cv2.imencode(".jpg", frame)[1].tobytes(), (600, 100, 100, 200))
    still = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert still.shape[1] < frame.shape[1] and box[2:] == [100, 200]


def test_telegram_gets_the_blurred_still(env):
    env.link(env.a, -1001)
    frame = cv2.imread(str(sample_path("messi5.jpg")))
    env.blobs.put("pool/frame.jpg", cv2.imencode(".jpg", frame)[1].tobytes(), "image/jpeg")
    face = max(found_faces(frame), key=lambda f: f[2] * f[3])
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, T0,
                                 picture_key="pool/frame.jpg", picture_box=[float(v) for v in face])
    env.run(T0)
    photo = env.fake.of("sendPhoto")[0][2]["photo"]
    still = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
    assert found_faces(still) == []  # the stranger's face does not leave the app
    assert inc_.get_incident(env.a, inc.id).telegram_message_id == "101"


def test_the_token_is_never_printed(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.link(env.a, -1001)
    env.fake.replies["sendMessage"] = [httpx.Response(400, json={"ok": False, "error_code": 400,
                                                                 "description": "Bad Request: wrong chat"})]
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "x", None, T0)
    env.fake.clock = T0
    env.worker.run_once(now=T0)
    with env.a.tx() as s:
        job = s.scalar(select(m.Job).where(m.Job.kind == "telegram.sync"))
    assert job.status == "queued" and "wrong chat" in caplog.text  # that group is tried again on its own

    def refuse(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")
    bot = TelegramBot(TOKEN, transport=httpx.MockTransport(refuse))
    with pytest.raises(TelegramError) as e:
        bot.get_me()
    assert TOKEN not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    assert TOKEN not in repr(bot) and TOKEN not in caplog.text
    assert TOKEN in env.fake.urls[0]  # it was used, in the address only


def test_a_busy_group_defers_and_429_waits_for_retry_after(env):
    env.link(env.a, -1001)
    env.fake.replies["sendMessage"] = [httpx.Response(429, json={
        "ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7",
        "parameters": {"retry_after": 7}})]
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "x", None, T0)
    env.run(T0)
    with env.a.tx() as s:
        job = s.scalar(select(m.Job).where(m.Job.kind == "telegram.sync"))
        assert (job.status, job.attempts, job.run_after) == ("queued", 0, at(7))
    env.run(at(6))
    assert len(env.fake.of("sendMessage")) == 1
    env.run(at(7))
    assert len(env.fake.of("sendMessage")) == 2 and inc_.get_incident(env.a, inc.id).telegram_message_id


def test_without_a_token_the_log_channel_stands_in(tmp_path, caplog):
    settings = Settings(_env_file=None, session_secret="s", data_dir=tmp_path)
    assert isinstance(components.make_alert_channel(settings), LogChannel)
    with_token = settings.model_copy(update={"telegram_bot_token": TOKEN})
    channel = components.make_alert_channel(with_token)
    assert isinstance(channel, TelegramChannel) and TOKEN not in repr(channel.bot)

    env = make_env(tmp_path / "e")
    caplog.set_level(logging.INFO, "watchful.telegram")
    open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, T0)
    env.run(T0)
    assert env.fake.calls == [] and "Telegram is not connected" in caplog.text
    assert "Someone is in the pool" in caplog.text


HOOK = "hook-secret-made-in-this-test"


def api_app(tmp_path, token: str | None = TOKEN):
    from accounts import browser
    from test_api import StubManager
    from watchful.agent.llm import FakeLLM
    from watchful.api.app import create_app
    s = Settings(_env_file=None, session_secret="s" * 32, data_dir=tmp_path, telegram_bot_token=token,
                 telegram_webhook_secret=HOOK, telegram_api_url="http://127.0.0.1:9")  # no real Telegram
    app = create_app(s, llm=FakeLLM([]), manager=StubManager())
    return app, browser(app, "asha@example.com", "ws_green_park", "admin"), \
        browser(app, "sunil@example.com", "ws_green_park", "supervisor"), \
        browser(app, "vik@example.com", "ws_green_park", "viewer")


def test_api_connects_a_group_through_the_webhook_and_acts_on_incidents(tmp_path):
    from fastapi.testclient import TestClient
    app, admin, supervisor, viewer = api_app(tmp_path)
    assert supervisor.post("/api/telegram/connect").status_code == 403
    made = admin.post("/api/telegram/connect").json()
    assert made["command"] == f"/connect {made['code']}"
    status = viewer.get("/api/telegram").json()
    assert (status["ready"], status["connected"], status["groups"], status["warnings"]) == (True, False, [], False)

    telegram = TestClient(app)  # Telegram itself: no session, only the secret
    update = {"update_id": 1, "message": {"chat": {"id": -1001, "type": "group", "title": "Guards"},
                                          "from": {"id": 7, "first_name": "Asha"}, "text": f"/connect {made['code']}"}}
    assert telegram.post("/telegram/webhook/wrong", json=update,
                         headers={"x-telegram-bot-api-secret-token": HOOK}).status_code == 404
    assert telegram.post(f"/telegram/webhook/{HOOK}", json=update).status_code == 404  # no header
    reply = telegram.post(f"/telegram/webhook/{HOOK}", json=update, headers={"x-telegram-bot-api-secret-token": HOOK})
    assert reply.status_code == 200 and reply.json()["text"].startswith("Connected to ws_green_park.")
    groups = viewer.get("/api/telegram").json()["groups"]
    assert [g["title"] for g in groups] == ["Guards"] and viewer.get("/api/telegram").json()["connected"]

    assert supervisor.post("/api/telegram/warnings", json={"on": True}).status_code == 403
    assert admin.post("/api/telegram/warnings", json={"on": True}).json() == {"warnings": True}
    assert viewer.get("/api/telegram").json()["warnings"] is True

    repo = app.state.ctx.repo_for("ws_green_park")
    cam = repo.add_camera(Camera(None, "Pool", "stream", "rtsp://pool")).id
    inc = open_or_touch_incident(repo, cam, "wat_x", "critical", "Someone is in the pool.", None, T0)
    listed = viewer.get("/api/incidents?status=open").json()
    assert [i["id"] for i in listed] == [inc.id] and listed[0]["reason"] == "Someone is in the pool."
    assert viewer.post(f"/api/incidents/{inc.id}/acknowledge").status_code == 403
    done = supervisor.post(f"/api/incidents/{inc.id}/acknowledge").json()
    assert (done["status"], done["ack_by"], done["changed"]) == ("acknowledged", "sunil@example.com", True)
    assert supervisor.post(f"/api/incidents/{inc.id}/false_alarm").json()["status"] == "false_alarm"
    assert viewer.get("/api/incidents/precision").json()["wat_x"]["false_alarm"] == 1
    assert supervisor.post("/api/incidents/inc_nope/acknowledge").status_code == 404

    link = groups[0]["id"]
    assert admin.delete(f"/api/telegram/groups/{link}").json() == {"ok": True}
    assert viewer.get("/api/telegram").json()["groups"] == []


def test_api_without_a_token_says_telegram_is_not_connected(tmp_path):
    app, admin, _, viewer = api_app(tmp_path, token=None)
    status = viewer.get("/api/telegram").json()
    assert (status["ready"], status["connected"], status["bot_username"]) == (False, False, None)
    assert status["message"] == "Telegram is not connected. Alerts show here in the app."
    refused = admin.post("/api/telegram/connect")
    assert refused.status_code == 409 and "bot token" in refused.json()["detail"]


# fix round 1


def noisy(h=400, w=300) -> np.ndarray:
    return np.random.default_rng(2).integers(0, 255, (h, w, 3), np.uint8)


def test_still_blurs_the_head_of_a_person_whose_face_is_not_found(env):
    """I3: a person from above or in profile has no face YuNet finds; their head is blurred anyway, because the
    track's class reaches the incident."""
    from test_rules import engine, tick, track
    from watchful.rules.presets import preset
    t0 = 1_800_000_000.0
    fell = engine(preset("fall"))
    fell.on_tick("cam", tick(t0, [track(1, posture="falling")]), t0)
    assert [c.cls for c in fell.on_tick("cam", tick(t0 + 1, [track(1, posture="fallen")]), t0 + 1)] == ["person"]

    from watchful.alerts.telegram import still_for
    from watchful.jobs import JobContext
    frame = noisy()
    env.blobs.put("pool/person.jpg", cv2.imencode(".png", frame)[1].tobytes(), "image/png")
    env.blobs.put("pool/kf.jpg", cv2.imencode(".png", frame)[1].tobytes(), "image/png")
    def person(camera: str) -> Candidate:
        return Candidate("wat_fall", env.cams[camera], T0.timestamp(), 1, "critical", {}, ["pool/kf.jpg"], "fp1",
                         "Fall", cls="person")
    # the frame around a person box (the box grown by its margin is the whole 300 by 400 frame here)
    framed = inc_.open_before_check(env.a, person("pool"), "pool/person.jpg", [50.0, 20.0, 200.0, 360.0])
    keyframe_only = inc_.open_before_check(env.a, person("gate"))  # the keyframe is the person's own box
    assert framed.agent_json["cls"] == keyframe_only.agent_json["cls"] == "person"
    ctx = JobContext(None, env.worker, 30, T0)
    for inc, head, body in ((framed, (60, 30, 180, 90), (60, 250, 180, 100)),
                            (keyframe_only, (20, 10, 260, 100), (20, 250, 260, 120))):
        out = cv2.imdecode(np.frombuffer(still_for(ctx, env.a, inc), np.uint8), cv2.IMREAD_COLOR)
        assert out.shape == frame.shape
        assert sharpness(out, head) < 0.1 * sharpness(frame, head)
        assert sharpness(out, body) > 0.5 * sharpness(frame, body)


def test_one_failing_group_never_holds_up_the_others(env):
    """I4: group 1 answers 400; group 2 still gets the critical alert, its reminder and the summary, and group 1 is
    tried again on its own."""
    env.link(env.a, -1001, "Guards")
    env.link(env.a, -1002, "Committee")
    bad = lambda: httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: oops"})
    env.fake.replies[("sendMessage", "-1001")] = [bad(), bad()]
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, T0)
    env.run(T0)
    assert [c[2]["chat_id"] for c in env.fake.of("sendMessage")] == ["-1001", "-1002"]
    env.run(at(15))
    sent = [c for c in env.fake.of("sendMessage") if c[0] == at(15)]
    assert [c[2]["chat_id"] for c in sent] == ["-1001"]  # only the failing group, tried again; it fails again
    env.run(at(30))
    with env.a.tx() as s:
        chats = sorted(s.scalars(select(m.TelegramMessage.chat_id).where(m.TelegramMessage.incident_id == inc.id)))
    assert chats == ["-1001", "-1002"]

    env.fake.replies[("sendMessage", "-1001")] = [bad()]
    env.run(at(120))
    reminders = [c for c in env.fake.of("sendMessage") if "Still not" in c[2]["text"]]
    assert [c[2]["chat_id"] for c in reminders] == ["-1001", "-1002"] and reminders[1][0] == at(120)
    env.run(at(135))
    reminders = [c for c in env.fake.of("sendMessage") if "Still not" in c[2]["text"]]
    assert sorted(c[2]["chat_id"] for c in reminders) == ["-1001", "-1001", "-1002"]
    inc_.acknowledge(env.a, inc.id, inc_.Actor("u", "Ravi"), at(140))
    for second in range(140, 150):
        env.run(at(second))

    from watchful.alerts.summary import queue_summaries
    nine = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
    env.fake.replies[("sendMessage", "-1001")] = [bad()]
    queue_summaries(env.worker, nine)
    env.run(nine)
    summaries = [c for c in env.fake.of("sendMessage") if c[2]["text"].startswith("Daily summary")]
    assert [c[2]["chat_id"] for c in summaries] == ["-1001", "-1002"]


def test_a_group_that_became_a_supergroup_is_followed(env):
    env.link(env.a, -1001)
    env.fake.replies[("sendMessage", "-1001")] = [httpx.Response(400, json={
        "ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
        "parameters": {"migrate_to_chat_id": -100777}})]
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.", None, T0)
    env.run(T0)
    assert [c[2]["chat_id"] for c in env.fake.of("sendMessage")] == ["-1001", "-100777"]
    assert [g.chat_id for g in linked_groups(env.a, env.a.workspace_id)] == ["-100777"]
    with env.a.tx() as s:
        assert s.scalar(select(m.TelegramMessage.chat_id).where(m.TelegramMessage.incident_id == inc.id)) == "-100777"


def test_a_refused_still_goes_as_text(env):
    env.link(env.a, -1001)
    env.fake.replies["sendPhoto"] = [httpx.Response(400, json={"ok": False, "error_code": 400,
                                                               "description": "Bad Request: IMAGE_PROCESS_FAILED"})]
    inc = open_or_touch_incident(env.a, env.cams["pool"], "wat_pool", "critical", "Someone is in the pool.",
                                 env.picture(), T0)
    env.run(T0)
    assert [c[1] for c in env.fake.calls] == ["sendPhoto", "sendMessage"]
    inc_.acknowledge(env.a, inc.id, inc_.Actor("u", "Ravi"), at(5))
    env.run(at(5))
    assert env.fake.calls[-1][1] == "editMessageText"  # edited as the text it is


def test_webhook_secret_rules(tmp_path):
    """M-b and M-c: a secret Telegram would refuse is refused at start; a path that is not ASCII is a 404."""
    from fastapi.testclient import TestClient
    with pytest.raises(ValueError):
        Settings(_env_file=None, session_secret="s" * 32, telegram_webhook_secret="has spaces and é")
    assert Settings(_env_file=None, session_secret="s" * 32, telegram_webhook_secret="").telegram_webhook_secret is None
    app, *_ = api_app(tmp_path)
    hook = TestClient(app, raise_server_exceptions=False)
    res = hook.post("/telegram/webhook/caf%C3%A9", json={}, headers={"x-telegram-bot-api-secret-token": HOOK})
    assert res.status_code == 404


def test_one_face_model_for_every_society(env, monkeypatch):
    """M-e: the worker loads YuNet and SFace once, not once per society."""
    from watchful import components
    from watchful.alerts.telegram import still_for
    from watchful.jobs import JobContext
    made = []
    real = components.make_faces
    monkeypatch.setattr(components, "make_faces", lambda *a, **k: made.append(1) or real(*a, **k))
    ctx = JobContext(None, env.worker, 30, T0)
    for repo, cam in ((env.a, env.cams["pool"]), (env.b, env.cams["b_gate"])):
        inc = open_or_touch_incident(repo, cam, "w", "critical", "x", env.picture(f"{cam}/kf.jpg"), T0)
        assert still_for(ctx, repo, inc)
    assert len(made) == 1


def test_a_slow_group_does_not_hold_up_another(env):
    """M-f: calls are one at a time per group, not across groups."""
    import threading
    from watchful.alerts.telegram import ChatLimiter, _call
    limiter = ChatLimiter(env.worker.tuning.alerts)
    inside, release = threading.Event(), threading.Event()

    def slow():
        inside.set()
        release.wait(5)
        return "a"
    t = threading.Thread(target=lambda: _call(limiter, "-1", T0, slow))
    t.start()
    assert inside.wait(5)
    done = []
    other = threading.Thread(target=lambda: done.append(_call(limiter, "-2", T0, lambda: "b")))
    other.start()
    other.join(2)
    finished = list(done)  # while group -1 is still in its slow call
    release.set()
    t.join(5)
    assert finished == [("b", None)]
