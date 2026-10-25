"""A fake Bot API (httpx MockTransport) and two societies with a job worker, for the incident and Telegram tests."""
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import cv2
import httpx
import numpy as np

from watchful.alerts.telegram import TelegramBot, TelegramChannel, connect_code, handle_update
from watchful.config import Settings
from watchful.db.session import make_engine, migrate
from watchful.jobs import JobQueue, Worker
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import Camera
from watchful.store.sql import SqlRepository, create_workspace
from watchful.tuning import load_tuning

TOKEN = "123456:test-token-made-in-the-test"
T0 = datetime(2026, 10, 25, 16, 30, tzinfo=UTC)  # 10 PM in Kolkata


def _multipart(request: httpx.Request) -> dict:
    boundary = re.search(r"boundary=([^;]+)", request.headers["content-type"]).group(1).encode()
    out = {}
    for part in request.content.split(b"--" + boundary):
        head, _, body = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head)
        if not name:
            continue
        body = body[:-2] if body.endswith(b"\r\n") else body
        out[name.group(1).decode()] = body if b"filename=" in head else body.decode()
    for key in ("reply_markup", "reply_parameters"):
        if key in out:
            out[key] = json.loads(out[key])
    return out


class FakeTelegram:
    """Answers Bot API calls like Telegram and records each with the test's clock."""

    def __init__(self):
        self.calls: list[tuple[datetime | None, str, dict]] = []
        self.clock: datetime | None = None
        self.next_id = 100
        self.replies: dict[str, list[httpx.Response]] = {}
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("multipart/"):
            data = _multipart(request)
        else:
            data = json.loads(request.content or b"{}")
        self.calls.append((self.clock, method, data))
        if self.replies.get(method):
            return self.replies[method].pop(0)
        if method.startswith("send"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def of(self, *methods: str) -> list[tuple[datetime | None, str, dict]]:
        return [c for c in self.calls if c[1] in methods]


@dataclass
class Env:
    engine: object
    a: SqlRepository
    b: SqlRepository
    fake: FakeTelegram
    worker: Worker
    blobs: LocalBlobStore
    cams: dict = field(default_factory=dict)

    def repo_for(self, workspace_id: str) -> SqlRepository:
        return {self.a.workspace_id: self.a, self.b.workspace_id: self.b}[workspace_id]

    def link(self, repo, chat_id: int, title: str = "Guards", now=T0) -> dict | None:
        code, _ = connect_code(self.a, repo.workspace_id, now)
        return handle_update(self.a, self.repo_for, {"message": {
            "chat": {"id": chat_id, "type": "supergroup", "title": title}, "from": {"id": 7, "first_name": "Asha"},
            "text": f"/connect {code}"}}, now)

    def tap(self, chat_id: int, message_id, data: str, user=(42, "Ravi"), now=T0) -> dict | None:
        return handle_update(self.a, self.repo_for, {"callback_query": {
            "id": "cb1", "from": {"id": user[0], "first_name": user[1]}, "data": data,
            "message": {"message_id": int(message_id), "chat": {"id": chat_id, "type": "supergroup"}}}}, now)

    def run(self, now: datetime) -> list:
        self.fake.clock = now
        return self.worker.run_due(now)

    def picture(self, key: str = "cam/kf/1.jpg") -> str:
        img = np.full((360, 480, 3), 90, np.uint8)
        cv2.rectangle(img, (200, 120), (280, 330), (40, 40, 40), -1)
        self.blobs.put(key, cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")
        return key


def make_env(tmp_path, tuning_overrides: dict | None = None) -> Env:
    engine = make_engine(f"sqlite:///{tmp_path / 'alerts.db'}")
    migrate(engine)
    wa, wb = create_workspace(engine, "Green Park"), create_workspace(engine, "Lake View")
    a, b = SqlRepository(engine, wa.id, secret="s"), SqlRepository(engine, wb.id, secret="s")
    fake = FakeTelegram()
    blobs = LocalBlobStore(tmp_path / "blobs")
    settings = Settings(_env_file=None, session_secret="s", data_dir=tmp_path / "data")
    channel = TelegramChannel(bot=TelegramBot(TOKEN, transport=httpx.MockTransport(fake)))
    worker = Worker(JobQueue(engine), settings, load_tuning(overrides=tuning_overrides),
                    repo_for=lambda ws: {wa.id: a, wb.id: b}[ws], blobs=blobs, channel=channel, error_wait_s=0)
    env = Env(engine, a, b, fake, worker, blobs)
    env.cams = {"pool": a.add_camera(Camera(None, "Pool", "stream", "rtsp://pool")).id,
                "gate": a.add_camera(Camera(None, "Main gate", "stream", "rtsp://gate")).id,
                "b_gate": b.add_camera(Camera(None, "Gate", "stream", "rtsp://b")).id}
    return env
