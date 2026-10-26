"""The judges' demo: a sandbox society per "Try the demo", its demo cameras looping clips as if live with a seeded
day, the visitor's own camera, the Telegram preview, the caps and the daily reset, through the real app."""
import json
import time
from datetime import timedelta

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from test_api import StubManager
from watchful import demo, demo_rules
from watchful.agent.llm import AILimitReached, FakeLLM
from watchful.alerts import incidents as inc_
from watchful.alerts import telegram
from watchful.api import create_app
from watchful.billing.limits import entitlement, over_allowance
from watchful.config import Settings
from watchful.db import models as m
from watchful.ingest.loop import LOOPS, Loops, demo_source
from watchful.jobs import JobQueue, Worker
from watchful.rules.engine import validate
from watchful.tuning import Tuning, load_tuning
from watchful.vision.models import sample_path
from watchful.workers import should_record

BASE = "http://localhost:8000"


class Manager(StubManager):
    """The camera manager as the routes see it, remembering what was started, stopped and pushed."""

    def __init__(self):
        super().__init__()
        self.stopped, self.pushed, self.camera_ws = [], [], {}

    def start(self, camera_id, workspace_id=None):
        super().start(camera_id, workspace_id)
        self.camera_ws[camera_id] = workspace_id

    def stop(self, camera_id):
        self.stopped.append(camera_id)
        self.camera_ws.pop(camera_id, None)

    def push_frame(self, camera_id, jpeg, ts):
        self.pushed.append(camera_id)


def make(tmp_path, **limits):
    tuning = load_tuning(overrides={"limits": limits}) if limits else Tuning()
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, trust_cloudflare=True, _env_file=None)
    manager = Manager()
    return create_app(s, llm=FakeLLM([]), manager=manager, tuning=tuning), manager


def judge(app, ip="203.0.113.5", origin=BASE) -> TestClient:
    """A visitor's browser behind Cloudflare, which names the visitor's address."""
    return TestClient(app, base_url=BASE, headers={"origin": origin, "cf-connecting-ip": ip})


def me(client) -> dict:
    res = client.get("/api/me")
    assert res.status_code == 200, res.text
    return res.json()


def jpeg(image) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


def small_clip(path, seconds: float = 3.0, fps: int = 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    for i in range(int(seconds * fps)):
        frame = np.full((120, 160, 3), 40, np.uint8)
        cv2.circle(frame, (10 + i * 4 % 140, 60), 10, (255, 255, 255), -1)
        out.write(frame)
    out.release()
    return path


def test_demo_login_gives_a_sandbox_of_its_own(tmp_path):
    app, manager = make(tmp_path, demo_logins_per_ip=2)
    ctx = app.state.ctx
    a = judge(app)
    res = a.post("/api/demo/login")
    assert res.status_code == 201, res.text
    assert res.json()["next"] == "/app"
    seen = me(a)
    assert seen["user"]["demo"] is True and seen["user"]["email"] is None and seen["role"] == "owner"
    ws = seen["workspace"]
    assert ws["demo"] == "sandbox" and ws["plan"] == "society" and ws["expires_at"]

    # only the demo cameras whose clip exists: Plaza on OpenCV's vtest.avi; Pool and the gates wait for their clips
    cams = a.get("/api/cameras").json()
    assert [c["name"] for c in cams] == ["Plaza"] and cams[0]["kind"] == "demo"
    assert cams[0]["url"].endswith("vtest.avi")
    assert set(manager.started) == {cams[0]["id"]}  # started, resting until someone watches
    site = [w for w in ctx.repo_for(ws["id"]).list_watches() if w.camera_id is None]
    assert [(w.text, w.watch["conditions"][0]["signal"]) for w in site] == [("A camera is covered or offline",
                                                                            "camera_health")]
    names = sorted(w["text"] for w in a.get(f"/api/cameras/{cams[0]['id']}/watches").json())
    assert names == ["A camera is covered or offline", "log people leaving the left walkway",
                     "someone walks into the left walkway", "too many people in the left walkway"]
    info = a.get("/api/demo").json()
    assert info["clips"] == [{"camera": "Plaza", "source": "OpenCV's sample video vtest.avi, BSD licence."}]
    assert (info["own_cameras"], info["own_cameras_allowed"], info["ended"]) == (0, 2, False)

    # a day of incidents, each with a picture cut from the clip, and a few log rows
    feed = a.get("/api/incidents").json()
    assert len(feed) == 6 and all(i["picture_url"] for i in feed)
    assert sorted(i["status"] for i in feed) == ["acknowledged", "acknowledged", "false_alarm", "open", "resolved",
                                                 "resolved"]
    opened = [i for i in feed if i["status"] == "open"][0]
    assert opened["severity"] == "critical" and opened["title"] == "someone walks into the left walkway"
    ages = [time.time() - _epoch(i["opened_at"]) for i in feed]
    assert 0 < min(ages) < 3600 and 20 * 3600 < max(ages) < 24 * 3600
    acked = [i for i in feed if i["status"] == "acknowledged" and i["ack_by"] == "Anita"][0]
    steps = a.get(f"/api/incidents/{acked['id']}").json()["timeline"]
    assert {"kind": "acknowledged", "by": "Anita", "via": "telegram"}.items() <= steps[1].items()
    repo = ctx.repo_for(ws["id"])
    logs = repo.query_events(camera_id=cams[0]["id"], kinds=["watch_log"])
    assert len(logs) == 5 and all(e.data["watch"] == "Leaves left walkway" for e in logs)
    with ctx.db.tx() as s:
        keys = list(s.scalars(select(m.Incident.picture_key).where(m.Incident.workspace_id == ws["id"])))
    assert all(k.startswith(f"{ws['id']}/incidents/") and ctx.blobs.get(k)[:2] == b"\xff\xd8" for k in keys)
    # a demo camera of a sandbox never records: its day is seeded, so a new sandbox costs no analysis
    assert should_record(repo, repo.get_camera(cams[0]["id"])) is False

    # comped while it lasts; the plan sweep leaves it alone
    with ctx.db.tx() as s:
        row = s.get(m.Workspace, ws["id"])
        ent = entitlement(row)
        assert (ent.source, ent.plan.id, ent.cameras) == ("comp", "society", 2) and row.comp_until == row.expires_at
    assert over_allowance(ctx.db, ws["id"]).cameras == []

    # pressing Try the demo again keeps the same sandbox
    assert a.post("/api/demo/login").status_code == 201
    assert me(a)["workspace"]["id"] == ws["id"]

    # a second judge at the same time gets a sandbox of their own and never sees the first one's
    b = judge(app, ip="198.51.100.7")
    assert b.post("/api/demo/login").status_code == 201
    other = me(b)["workspace"]
    assert other["id"] != ws["id"] and other["demo"] == "sandbox"
    assert {c["id"] for c in b.get("/api/cameras").json()}.isdisjoint({c["id"] for c in cams})
    assert b.get(f"/api/incidents/{opened['id']}").status_code == 404
    assert b.get(f"/api/incidents/{opened['id']}/telegram").status_code == 404
    assert b.get(f"/api/cameras/{cams[0]['id']}").status_code == 404

    # the limit per address a day, then a plain refusal; an IPv6 visitor is one /64
    assert judge(app).post("/api/demo/login").status_code == 201
    refused = judge(app).post("/api/demo/login")
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 3600
    assert refused.json() == {"detail": "This address has started 2 demos today, the most for one day. Come back "
                                        "tomorrow."}
    assert judge(app, ip="2001:db8:1:2::5").post("/api/demo/login").status_code == 201
    assert judge(app, ip="2001:db8:1:2:abcd::9").post("/api/demo/login").status_code == 201
    assert judge(app, ip="2001:db8:1:2::77").post("/api/demo/login").status_code == 429
    assert judge(app, ip="2001:db8:1:3::1").post("/api/demo/login").status_code == 201
    # a login started by another site is refused
    assert judge(app, ip="192.0.2.10", origin="https://evil.example").post("/api/demo/login").status_code == 403

    # a demo visitor cannot make a society that outlives the demo, invite anyone or buy anything
    assert a.post("/api/workspaces", json={"name": "Kept"}).status_code == 403
    assert a.post("/api/invites", json={"role": "admin"}).status_code == 403
    assert a.get("/api/billing").json()["can_checkout"] is False
    assert a.post("/api/billing/checkout", json={"plan": "society", "period": "month", "cameras": 3}).status_code == 403
    # uploads and enrolling faces are off; two cameras of the visitor's own, then a plain refusal
    up = a.post("/api/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    assert up.status_code == 403 and "demo" in up.json()["detail"]
    enrol = a.post("/api/people", data={"name": "Asha"}, files={"files": ("a.jpg", b"x", "image/jpeg")})
    assert enrol.status_code == 403 and "consent" in enrol.json()["detail"]
    tg = a.post("/api/telegram/connect")
    assert tg.status_code == 403 and "Telegram is off in the demo" in tg.json()["detail"]
    for name in ("This laptop", "My phone"):
        made = a.post("/api/cameras", json={"name": name, "kind": "webcam"})
        assert made.status_code == 200 and made.json()["enabled"] is True, made.text
    third = a.post("/api/cameras", json={"name": "Another", "kind": "webcam"})
    assert third.status_code == 403 and third.json()["detail"] == "The demo takes 2 cameras of your own. Sign in to " \
                                                                  "add more."
    with ctx.db.tx() as s:
        s.get(m.Workspace, ws["id"]).comp_cameras = 1  # over its plan: the sweep still leaves a sandbox alone
    assert over_allowance(ctx.db, ws["id"]).cameras == []
    assert all(c["enabled"] for c in a.get("/api/cameras").json())

    # who is looking at a public page; a member signed in is never signed out without saying so
    assert judge(app, ip="192.0.2.50").get("/api/demo/visitor").json() == {"kind": "signed_out"}
    assert a.get("/api/demo/visitor").json() == {"kind": "demo"}
    from accounts import browser
    member = browser(app, "owner@example.com", "ws_green_park")
    assert member.get("/api/demo/visitor").json() == {"kind": "member"}
    asked = member.post("/api/demo/login", headers={"cf-connecting-ip": "198.51.100.200"})
    assert asked.status_code == 409 and "signs you out" in asked.json()["detail"]
    left = member.post("/api/demo/login", json={"sign_out": True}, headers={"cf-connecting-ip": "198.51.100.200"})
    assert left.status_code == 201 and me(member)["user"]["demo"] is True


def _epoch(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value).timestamp()


def test_dropping_a_recorded_clip_adds_its_camera(tmp_path):
    """The owner's consented clips go in samples/demo/ under demo.json's names: new sandboxes get those cameras with
    no code change. A camera without its clip is left out, never shown with another clip."""
    app, _ = make(tmp_path)
    settings = app.state.ctx.settings
    assert [c.key for c in demo.demo_cameras(settings)] == ["plaza"]
    assert {c["key"] for c in demo.missing_cameras(settings)} == {"pool", "main_gate", "service_gate"}
    small_clip(tmp_path / "samples" / "demo" / "pool.mp4")
    assert [c.key for c in demo.demo_cameras(settings)] == ["pool", "plaza"]
    a = judge(app)
    assert a.post("/api/demo/login").status_code == 201
    cams = {c["name"]: c for c in a.get("/api/cameras").json()}
    assert set(cams) == {"Pool", "Plaza"} and cams["Pool"]["url"].endswith("demo/pool.mp4")
    assert [z["name"] for z in cams["Pool"]["zones"]] == ["water", "lifeguard chair"]
    assert len([i for i in a.get("/api/incidents").json() if i["camera_id"] == cams["Pool"]["id"]]) == 1


def test_demo_json_is_honest_and_every_watch_can_run():
    cameras = demo.spec()["cameras"]
    assert {c["key"] for c in cameras} == {"pool", "main_gate", "service_gate", "plaza"}
    clips = [c["clip"] for c in cameras]
    assert len(set(clips)) == len(clips)  # no two cameras share a clip: a camera is never faked with another's
    assert "speed_1080p.mp4" not in clips  # vtest.avi again, upscaled (eval/make_speed_clip.sh): not another camera
    for c in cameras:
        assert c["source"] and c["clip"] == "vtest.avi" or c["clip"].startswith("demo/")
        kinds = {z["name"]: z.get("kind", "area") for z in c["zones"]}
        for entry in c["watches"]:
            w = demo.watch_of(entry)
            assert validate(w, kinds) == [], (c["key"], entry["text"])
            assert not any(ch in entry["text"] for ch in "—–-")  # no dashes in what people read
        for inc in c["incidents"]:
            assert 0 < inc["hours_ago"] < 24 and inc["status"] in ("open", "acknowledged", "false_alarm", "resolved")
            assert demo.watch_of(c["watches"][inc["watch"]]).output == "alert"
        for row in c["log_rows"]:
            assert demo.watch_of(c["watches"][row["watch"]]).output == "log"


def test_demo_cameras_share_one_decoder_per_clip(tmp_path):
    clip = small_clip(tmp_path / "loop.mp4", seconds=2.0)
    loops = Loops()
    a, b = loops.tap(str(clip), 10.0, 960), loops.tap(str(clip), 10.0, 960)
    assert loops.decoders() == 1 and a.loop is b.loop
    seen_a, seen_b, positions = [], [], []
    deadline = time.time() + 8
    while time.time() < deadline and (len(seen_a) < 25 or len(seen_b) < 25):
        fa, fb = a.read(0.5), b.read(0.5)
        if fa is not None:
            seen_a.append(fa)
            positions.append(fa.pos)
        if fb is not None:
            seen_b.append(fb)
    assert len(seen_a) >= 25 and len(seen_b) >= 25
    assert any(later < earlier for earlier, later in zip(positions, positions[1:]))  # it looped
    assert seen_a[-1].image is not seen_b[-1].image  # each camera has its own copy to draw on
    assert abs(seen_a[-1].ts - time.time()) < 2  # stamped as a live camera's frame
    a.close()
    assert loops.decoders() == 1
    b.close()
    assert loops.decoders() == 0
    # read faster than real time (tests and tools), each camera decodes on its own and sees every frame
    assert demo_source(str(clip), 10.0, 960, realtime=False).__class__.__name__ == "FileSource"
    assert LOOPS is not loops


def test_demo_cameras_loop_and_raise_their_seeded_watches(tmp_path):
    """A sandbox's Plaza camera, run as the camera processes run it, raises its seeded watch on the clip."""
    from watchful.pool import CameraJob, PoolWorker
    from watchful.rules.watch import select_watches
    app, _ = make(tmp_path)
    ctx = app.state.ctx
    a = judge(app)
    a.post("/api/demo/login")
    ws = me(a)["workspace"]["id"]
    repo = ctx.repo_for(ws)
    cam = repo.list_cameras()[0]
    watches, problems = select_watches(repo.list_watches(cam.id), cam.id, cam.zones)
    assert problems == [] and len(watches) == 4
    sent = []
    s = ctx.settings.model_copy(update={"demo_realtime": False, "analysis_fps": 5.0, "display_fps": 5.0})
    worker = PoolWorker(0, s, Tuning(), sent.append, blobs=ctx.blobs)
    worker.assign(CameraJob(cam, watches, [], record=should_record(repo, cam)))
    for _ in range(20):  # nobody watching: it rests, reading and analysing nothing
        worker.step()
    assert worker.cameras[cam.id].pipe.stats().get("frames", 0) == 0
    assert worker.cameras[cam.id].status()["source_status"] == "paused"
    worker.command(("viewers", cam.id, 1))  # a judge opens the camera
    deadline = time.time() + 120
    while time.time() < deadline and not any(x["type"] == "candidate" for x in sent):
        worker.step()
    worker.close()
    found = [x["pack"].candidate for x in sent if x["type"] == "candidate"]
    assert found and found[0].watch_name == "Someone in the left walkway" and found[0].severity == "critical"
    assert all(x.get("workspace_id") == ws for x in sent if x["type"] == "candidate")
    assert not [x for x in sent if x["type"] == "events"]  # it never records: its day is seeded


def test_a_visitors_own_camera_and_its_watch(tmp_path):
    """Add your camera: this laptop or a phone by the device link's QR code; a watch on it fires and files an
    incident in the visitor's own sandbox."""
    from watchful.pool import CameraJob, PoolWorker
    from watchful.rules.watch import select_watches
    from watchful.workers import WorkerManager
    app, manager = make(tmp_path)
    ctx = app.state.ctx
    a = judge(app)
    a.post("/api/demo/login")
    ws = me(a)["workspace"]["id"]
    phone = a.post("/api/cameras", json={"name": "My phone", "kind": "webcam", "purpose": "other"}).json()
    link = a.post(f"/api/cameras/{phone['id']}/device-token").json()
    assert link["url"].startswith(f"{BASE}/cam/") and link["qr_svg"].startswith("<svg")
    with a.websocket_connect(f"/ws/cam/{link['token']}") as sock:
        hello = sock.receive_json()
        assert hello["type"] == "hello" and hello["camera"] == "My phone" and hello["blur"] is True
        sock.send_bytes(jpeg(np.zeros((48, 64, 3), np.uint8)))
        deadline = time.time() + 5
        while phone["id"] not in manager.pushed and time.time() < deadline:
            time.sleep(0.05)
    assert phone["id"] in manager.pushed and phone["id"] in manager.started

    typed = {"name": "Two or more people", "conditions": [{"signal": "count", "op": ">=", "value": 2}],
             "severity": "warning", "verify": "Are there two or more people?"}
    saved = a.post(f"/api/cameras/{phone['id']}/watches",
                   json={"text": "two or more people in view", "watch": typed}).json()
    assert saved["saved"] is True, saved
    repo = ctx.repo_for(ws)
    cam = repo.get_camera(phone["id"])
    watches, _ = select_watches(repo.list_watches(cam.id), cam.id, cam.zones)
    sent = []
    worker = PoolWorker(0, ctx.settings, Tuning(), sent.append, blobs=ctx.blobs)
    worker.assign(CameraJob(cam, watches, []))
    runner = worker.cameras[cam.id]
    clip = cv2.VideoCapture(str(sample_path("vtest.avi")))
    clip.set(cv2.CAP_PROP_POS_MSEC, 28_000)  # a busy moment of the plaza, as a phone held up to it would see
    deadline = time.time() + 60
    while time.time() < deadline and not any(x["type"] == "candidate" for x in sent):
        ok, frame = clip.read()
        if not ok:
            break
        runner.source.push(jpeg(frame), time.time())
        worker.step(0.2)
        time.sleep(0.2)
    worker.close()
    packs = [x["pack"] for x in sent if x["type"] == "candidate"]
    assert packs and packs[0].candidate.camera_id == phone["id"]
    alert = json.dumps({"tool": "raise_alert", "args": {"summary": "Three people in view."}, "why": "."})
    real = WorkerManager(ctx.settings.model_copy(update={"index_embeddings": False, "scan_pool_at_start": False}),
                         Tuning(), None, ctx.blobs, llm=FakeLLM([alert] * 4), repo_for=ctx.repo_for,
                         societies=ctx.workspace_ids, processes=1)
    try:
        real.camera_ws[phone["id"]] = ws
        real._handle({"type": "candidate", "workspace_id": ws, "pack": packs[0]})
        deadline = time.time() + 20
        while time.time() < deadline and not inc_.list_incidents(repo, camera_id=phone["id"]):
            time.sleep(0.2)
    finally:
        real.shutdown()
    mine = [i for i in a.get("/api/incidents").json() if i["camera_id"] == phone["id"]]
    assert len(mine) == 1 and mine[0]["watch_id"] == saved["id"]


def test_the_telegram_preview_is_the_groups_own_message(tmp_path):
    app, _ = make(tmp_path)
    ctx = app.state.ctx
    a = judge(app)
    a.post("/api/demo/login")
    ws = me(a)["workspace"]["id"]
    repo = ctx.repo_for(ws)
    opened = [i for i in a.get("/api/incidents").json() if i["status"] == "open"][0]
    shown = a.get(f"/api/incidents/{opened['id']}/telegram").json()
    inc = inc_.get_incident(repo, opened["id"])
    tz, _, _ = telegram._society(repo)
    assert shown["caption"] == telegram.caption(inc, "Plaza", tz, ctx.settings.public_base_url)
    assert shown["caption"].startswith("Critical, Plaza, ") and "Not acknowledged yet." in shown["caption"]
    assert [[b["text"] for b in row] for row in shown["buttons"]] == [["Acknowledge", "False alarm"]]
    assert (shown["goes_to_telegram"], shown["sent"], shown["connected"]) == (True, True, False)
    assert shown["photo"].startswith("data:image/jpeg;base64,")

    assert a.post(f"/api/incidents/{opened['id']}/acknowledge").status_code == 200
    after = a.get(f"/api/incidents/{opened['id']}/telegram").json()
    assert "Acknowledged by Demo visitor at" in after["caption"]
    assert [[b["text"] for b in row] for row in after["buttons"]] == [["False alarm"]]
    warning = [i for i in a.get("/api/incidents").json() if i["severity"] == "warning"][0]
    quiet = a.get(f"/api/incidents/{warning['id']}/telegram").json()
    assert quiet["goes_to_telegram"] is False and quiet["caption"].startswith("Warning, Plaza, ")


def test_the_demo_ai_caps(tmp_path, monkeypatch):
    """A sandbox's checks and help have the demo's own caps a day, checks counted once each however many steps; all
    sandboxes together stay under AI_DAILY_CALL_LIMIT; a real society keeps its plan's."""
    app, _ = make(tmp_path)
    ctx = app.state.ctx
    settings = ctx.settings.model_copy(update={"ai_daily_call_limit": 4})
    import watchful.components as components
    built = components.build
    monkeypatch.setattr(components, "build", lambda kind, name, **kw: FakeLLM(["ok"] * 20) if kind == "llm"
                        else built(kind, name, **kw))
    tuning = load_tuning(overrides={"limits": {"demo_ai_checks": 2, "demo_ai_assist": 1}})
    one, two = judge(app), judge(app, ip="198.51.100.7")
    one.post("/api/demo/login")
    two.post("/api/demo/login")
    ws1, ws2 = me(one)["workspace"]["id"], me(two)["workspace"]["id"]

    def llm(ws: str, task: str):
        return demo.sandbox_llm(FakeLLM(["ok"] * 20), ctx.repo_for(ws), settings, task, tuning)

    def check(ws: str) -> list[str]:
        one_check = llm(ws, "verify").check()
        return [one_check.complete("system", []) for _ in range(3)]  # three steps of one check

    assert check(ws1) == ["ok"] * 3 and check(ws1) == ["ok"] * 3
    with pytest.raises(AILimitReached):
        check(ws1)  # this sandbox's checks
    assert llm(ws1, "compile").complete("system", []) == "ok"
    with pytest.raises(AILimitReached):
        llm(ws1, "compile").complete("system", [])  # and its help, apart from its checks
    assert check(ws2) == ["ok"] * 3
    with pytest.raises(AILimitReached):
        check(ws2)  # its own cap has room, but every sandbox together is at AI_DAILY_CALL_LIMIT (4 a day)
    with ctx.db.tx() as s:
        assert s.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action == demo.AI_CALL)) == 4
    from accounts import browser
    browser(app, "owner@example.com", "ws_green_park")
    assert demo.sandbox_llm(FakeLLM(["ok"]), ctx.repo_for("ws_green_park"), settings, "verify", tuning) is None
    assert isinstance(components.make_llm(settings, ctx.repo_for(ws1), "verify"), demo.SandboxLLM)
    assert not isinstance(components.make_llm(settings, ctx.repo_for("ws_green_park"), "verify"), demo.SandboxLLM)


def test_a_sandbox_ends_and_the_daily_reset_deletes_it(tmp_path):
    app, manager = make(tmp_path)
    ctx = app.state.ctx
    old, new = judge(app), judge(app, ip="198.51.100.7")
    old.post("/api/demo/login")
    new.post("/api/demo/login")
    gone_ws, kept_ws = me(old)["workspace"]["id"], me(new)["workspace"]["id"]
    phone = old.post("/api/cameras", json={"name": "My phone", "kind": "webcam"}).json()
    token = old.post(f"/api/cameras/{phone['id']}/device-token").json()["token"]
    plaza = [c for c in old.get("/api/cameras").json() if c["kind"] == "demo"][0]
    ctx.blobs.put(f"{gone_ws}/{plaza['id']}/clips/x.mp4", b"clip", "video/mp4")
    with ctx.db.tx() as s:
        s.get(m.Workspace, gone_ws).expires_at = m.utcnow() - timedelta(minutes=1)

    # it ended: every request refused, its cameras stopped and switched off, its devices turned away
    over = old.get("/api/cameras")
    assert over.status_code == 403 and over.json()["detail"] == demo_rules.ENDED
    assert old.get("/api/demo").json()["ended"] is True
    stopped = app.state.sandboxes.run_once()
    assert set(stopped) >= {phone["id"], plaza["id"]} and phone["id"] in manager.stopped
    assert not any(c.enabled for c in ctx.repo_for(gone_ws).list_cameras())
    with old.websocket_connect(f"/ws/cam/{token}") as sock:
        with pytest.raises(Exception):
            sock.receive_json()
    # Try the demo again starts a new one
    assert old.post("/api/demo/login").status_code == 201
    fresh = me(old)["workspace"]["id"]
    assert fresh not in (gone_ws, kept_ws) and old.get("/api/cameras").status_code == 200

    # the daily reset deletes it with its visitor, device link and pictures, and nothing of the others
    worker = Worker(JobQueue(_engine(ctx)), ctx.settings, Tuning(), repo_for=ctx.repo_for, blobs=ctx.blobs)
    assert "demo_reset" in worker.schedule_daily()
    worker.run(until_idle=True)
    with ctx.db.tx() as s:
        assert s.get(m.Workspace, gone_ws) is None and s.get(m.Workspace, kept_ws) is not None
        links = select(func.count()).select_from(m.DeviceToken).where(m.DeviceToken.workspace_id == gone_ws)
        assert s.scalar(links) == 0
        assert s.scalar(select(func.count()).select_from(m.Incident).where(m.Incident.workspace_id == gone_ws)) == 0
        assert s.scalar(select(m.Job.status).where(m.Job.kind == "demo_reset")) == "done"
        assert s.scalar(select(m.Audit.data).where(m.Audit.action == "demo.reset"))["visitors"] == 1
    assert not (tmp_path / "blobs" / gone_ws).exists()
    assert (tmp_path / "blobs" / kept_ws).is_dir()
    from watchful.ingest.push import push_parts
    assert push_parts(ctx)[1].find(token) is None
    # cameras of a society the reset deleted stop in the app too
    manager.camera_ws["cam_left_over"] = gone_ws
    assert "cam_left_over" in app.state.sandboxes.run_once()


def _engine(ctx):
    from watchful.components import make_engine
    return make_engine(ctx.settings)
