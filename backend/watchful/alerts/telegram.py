"""Telegram: a society links its monitoring group with a one time code, and each incident gets one message there
(a still with faces of people who are not enrolled blurred, the reason, camera, time, a link to the clip behind sign
in, and Acknowledge and False alarm buttons), edited as the incident changes. Taps come back by webhook; a tap counts
only from a chat linked to the incident's own society.

Telegram allows about one message a second in a chat and 20 a minute in a group, then answers 429. Every call to a
chat goes through the job worker's ChatLimiter, count changes edit a message at most every Tuning
alerts.edit_every_s, and a job that must wait is deferred, so a situation that triggers all night is one message.

The bot token is in every Bot API address, so neither addresses nor httpx's request log are ever printed."""
import hashlib
import json
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import cv2
import httpx
import numpy as np
from sqlalchemy import delete, select

from watchful.alerts import incidents as inc_
from watchful.alerts.crops import blurred_crop
from watchful.db import models as m
from watchful.jobs import Defer, JobContext, handler, on_start
from watchful.registry import register
from watchful.tuning import AlertTuning, Tuning

log = logging.getLogger("watchful.telegram")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line prints the address, which holds the token

CAPTION_MAX, TEXT_MAX = 1024, 4096  # Bot API limits
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0, O, 1 or I
SEVERITY_WORD = {"critical": "Critical", "warning": "Warning", "info": "Note"}
UPDATES = ["message", "callback_query", "my_chat_member"]


class TelegramError(Exception):
    """A Bot API call that failed; the message never holds the token."""

    def __init__(self, method: str, description: str, code: int | None = None, retry_after: float | None = None):
        super().__init__(f"telegram {method}: {description}")
        self.method, self.description, self.code, self.retry_after = method, description, code, retry_after

    @property
    def gone(self) -> bool:
        """The chat or message is not there for the bot any more (removed from the group, message deleted)."""
        text = self.description.lower()
        return self.code == 403 or any(w in text for w in ("chat not found", "message to edit not found",
                                                            "message can't be edited", "bot was kicked"))


class TelegramBot:
    """The Bot API over HTTPS."""

    def __init__(self, token: str, api_url: str = "https://api.telegram.org", timeout_s: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=f"{api_url.rstrip('/')}/bot{token}/", timeout=timeout_s,
                                    transport=transport)

    def __repr__(self) -> str:
        return "TelegramBot()"

    def call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict | bool:
        try:
            if files:
                form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                        for k, v in (data or {}).items() if v is not None}
                r = self._client.post(method, data=form, files=files)
            else:
                r = self._client.post(method, json={k: v for k, v in (data or {}).items() if v is not None})
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TelegramError(method, type(e).__name__) from None  # never the request, whose address holds the token
        if not body.get("ok"):
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(method, str(body.get("description", "failed")), body.get("error_code"),
                                float(retry) if retry is not None else None)
        return body.get("result")

    def get_me(self) -> dict:
        return self.call("getMe")

    def set_webhook(self, url: str, secret: str) -> None:
        self.call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": UPDATES,
                                 "drop_pending_updates": False})


def keyboard(buttons: list | None) -> dict | None:
    if not buttons:
        return None
    rows = [[{"text": text, "url": action} if action.startswith(("https://", "http://"))
             else {"text": text, "callback_data": action} for text, action in row] for row in buttons]
    return {"inline_keyboard": rows}


@register("alerts", "telegram")
class TelegramChannel:
    """Sends and edits alert messages through a bot (TELEGRAM_BOT_TOKEN)."""
    live = True

    def __init__(self, settings=None, tuning: Tuning | None = None, name: str = "telegram",
                 bot: TelegramBot | None = None, **_):
        if bot is None:
            timeout = (tuning or Tuning()).alerts.http_timeout_s
            bot = TelegramBot(settings.telegram_bot_token, settings.telegram_api_url, timeout)
        self.bot = bot

    def send(self, chat_id: str, text: str, photo: bytes | None = None, buttons: list | None = None,
             reply_to: str | None = None) -> str:
        markup = keyboard(buttons)
        reply = {"message_id": int(reply_to), "allow_sending_without_reply": True} if reply_to else None
        if photo:
            result = self.bot.call("sendPhoto", {"chat_id": chat_id, "caption": text[:CAPTION_MAX],
                                                 "reply_markup": markup, "reply_parameters": reply},
                                   files={"photo": ("still.jpg", photo, "image/jpeg")})
        else:
            result = self.bot.call("sendMessage", {"chat_id": chat_id, "text": text[:TEXT_MAX], "reply_markup": markup,
                                                   "reply_parameters": reply,
                                                   "link_preview_options": {"is_disabled": True}})
        return str(result["message_id"])

    def edit(self, chat_id: str, message_id: str, text: str, buttons: list | None = None,
             photo: bool = False) -> None:
        markup = keyboard(buttons) or {"inline_keyboard": []}
        method, field, limit = ("editMessageCaption", "caption", CAPTION_MAX) if photo else \
            ("editMessageText", "text", TEXT_MAX)
        try:
            self.bot.call(method, {"chat_id": chat_id, "message_id": int(message_id), field: text[:limit],
                                   "reply_markup": markup})
        except TelegramError as e:
            if "not modified" not in e.description.lower():
                raise


class ChatLimiter:
    """Telegram's limits per chat, kept by the one worker that sends: a gap between messages, and no more than
    `per_min` in any minute; a 429's retry_after blocks the chat until then."""

    def __init__(self, cfg: AlertTuning):
        self.per_min, self.gap_s = cfg.chat_per_min, cfg.chat_gap_s
        self._sent: dict[str, deque] = defaultdict(deque)
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def hold(self, chat_id: str, now: datetime) -> float:
        """Seconds until this chat may get the next message; 0 when it may now."""
        t = now.timestamp()
        with self._lock:
            sent = self._sent[chat_id]
            while sent and sent[0] <= t - 60:
                sent.popleft()
            waits = [self._blocked.get(chat_id, t) - t]
            if sent:
                waits.append(sent[-1] + self.gap_s - t)
            if len(sent) >= self.per_min:
                waits.append(sent[0] + 60 - t)
            return max(0.0, *waits)

    def note(self, chat_id: str, now: datetime) -> None:
        with self._lock:
            self._sent[chat_id].append(now.timestamp())

    def block(self, chat_id: str, now: datetime, seconds: float) -> None:
        with self._lock:
            self._blocked[chat_id] = now.timestamp() + seconds


# linking a group


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def connect_code(db, workspace_id: str, now=None, tuning: AlertTuning | None = None) -> tuple[str, datetime]:
    """A new one time code for the society (any unused earlier one stops working); the group sends
    "/connect CODE" to link itself. Only the code's hash is kept."""
    t, now = tuning or AlertTuning(), inc_.when(now)
    code = "".join(secrets.choice(CODE_CHARS) for _ in range(t.code_length))
    expires = now + timedelta(minutes=t.code_minutes)
    with db.tx() as s:
        s.execute(delete(m.TelegramLink).where(m.TelegramLink.workspace_id == workspace_id,
                                               m.TelegramLink.chat_id.is_(None)))
        s.add(m.TelegramLink(workspace_id=workspace_id, code_hash=_hash(code), code_expires_at=expires))
    return code, expires


def linked_groups(db, workspace_id: str) -> list[m.TelegramLink]:
    with db.tx() as s:
        return list(s.scalars(select(m.TelegramLink).where(m.TelegramLink.workspace_id == workspace_id,
                                                           m.TelegramLink.chat_id.is_not(None))
                              .order_by(m.TelegramLink.connected_at)))


def disconnect(db, workspace_id: str, link_id: str) -> bool:
    with db.tx() as s:
        return s.execute(delete(m.TelegramLink).where(m.TelegramLink.workspace_id == workspace_id,
                                                      m.TelegramLink.id == link_id)).rowcount > 0


def _reply(chat_id, text: str) -> dict:
    return {"method": "sendMessage", "chat_id": chat_id, "text": text}


def _answer(callback_id: str, text: str, alert: bool = False) -> dict:
    return {"method": "answerCallbackQuery", "callback_query_id": callback_id, "text": text, "show_alert": alert}


def _person(user: dict | None) -> tuple[str, str]:
    user = user or {}
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p).strip() or user.get("username")
    return f"telegram:{user.get('id', 'unknown')}", (name or "Someone on Telegram")[:64]


def handle_update(db, repo_for, update: dict, now=None) -> dict | None:
    """One update from the webhook. Returns a Bot API method for the webhook's reply (a message to the group or the
    answer to a tap), or None. `db` reads the links of every society; `repo_for(workspace_id)` gives a society's
    repository."""
    now = inc_.when(now)
    if "callback_query" in update:
        return _on_tap(db, repo_for, update["callback_query"], now)
    member = update.get("my_chat_member")
    if member and (member.get("new_chat_member") or {}).get("status") in ("left", "kicked"):
        with db.tx() as s:  # the bot was removed: alerts can no longer go there
            s.execute(delete(m.TelegramLink).where(m.TelegramLink.chat_id == str(member["chat"]["id"])))
        return None
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@")[0].lower() if text else ""
    return _on_connect(db, msg, text, now) if command == "/connect" else None


def _on_connect(db, msg: dict, text: str, now: datetime) -> dict:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id"))
    parts = text.split()
    if len(parts) < 2:
        return _reply(chat_id, "Send /connect followed by the code from Settings, Telegram.")
    actor, _ = _person(msg.get("from"))
    title = chat.get("title") or _person(chat)[1]
    with db.tx() as s:
        linked = s.scalar(select(m.TelegramLink).where(m.TelegramLink.chat_id == chat_id))
        link = s.scalar(select(m.TelegramLink).where(m.TelegramLink.code_hash == _hash(parts[1]),
                                                     m.TelegramLink.chat_id.is_(None)))
        if link is None or link.code_expires_at is None or link.code_expires_at < now:
            if linked is not None:
                return _reply(chat_id, "This group is already connected.")
            return _reply(chat_id, "That code is not valid or has expired. Make a new one in Settings, Telegram.")
        if linked is not None and linked.workspace_id != link.workspace_id:
            return _reply(chat_id, "This group is connected to another society. Disconnect it there first.")
        if linked is not None:
            s.delete(link)
            return _reply(chat_id, "This group is already connected.")
        link.chat_id, link.title, link.connected_at = chat_id, title, now
        link.code_hash = link.code_expires_at = None
        name = s.scalar(select(m.Workspace.name).where(m.Workspace.id == link.workspace_id)) or "your society"
        s.add(m.Audit(workspace_id=link.workspace_id, actor=actor, action="telegram.connect", ts=now,
                      data={"chat": chat_id, "title": title}))
    return _reply(chat_id, f"Connected to {name}. Alerts will come to this group.")


def _on_tap(db, repo_for, cq: dict, now: datetime) -> dict:
    cid = cq.get("id", "")
    action, _, incident_id = (cq.get("data") or "").partition(":")
    message = cq.get("message") or {}
    chat_id, message_id = str((message.get("chat") or {}).get("id")), str(message.get("message_id"))
    if action not in ("ack", "false") or not incident_id:
        return _answer(cid, "That button is not known.")
    with db.tx() as s:
        workspace_id = s.scalar(select(m.TelegramLink.workspace_id).where(m.TelegramLink.chat_id == chat_id))
        ours = workspace_id is not None and s.scalar(select(m.TelegramMessage.id).where(
            m.TelegramMessage.workspace_id == workspace_id, m.TelegramMessage.chat_id == chat_id,
            m.TelegramMessage.message_id == message_id, m.TelegramMessage.incident_id == incident_id)) is not None
    if workspace_id is None:
        log.warning("a tap from chat %s, which is not linked to a society, was refused", chat_id)
        return _answer(cid, "This group is not connected to a society.", alert=True)
    if not ours:
        log.warning("a tap in chat %s on an alert of another society or message was refused", chat_id)
        return _answer(cid, "That alert does not belong to this group's society.", alert=True)
    who, name = _person(cq.get("from"))
    actor = inc_.Actor(who, name, "telegram")
    repo = repo_for(workspace_id)
    act = inc_.acknowledge if action == "ack" else inc_.false_alarm
    inc, changed = act(repo, incident_id, actor, now, chat=chat_id)
    if inc is None:
        return _answer(cid, "That alert is no longer here.")
    if changed:
        return _answer(cid, "Acknowledged. Thank you." if action == "ack" else "Closed as a false alarm.")
    if inc.status == inc_.ACKNOWLEDGED:
        return _answer(cid, f"Already acknowledged by {inc.ack_by}.")
    return _answer(cid, "This alert is already closed.")


# the incident's messages


def _clock(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%I:%M %p").lstrip("0") if dt else ""


def incident_link(base_url: str, incident_id: str) -> str:
    return f"{(base_url or '').rstrip('/')}/incidents/{incident_id}"


def _buttons_can_link(base_url: str) -> bool:
    return (base_url or "").startswith("https://")  # Telegram refuses buttons to plain http and localhost


def status_line(inc: m.Incident, tz: ZoneInfo) -> str:
    if inc.status == inc_.ACKNOWLEDGED:
        return f"Acknowledged by {inc.ack_by} at {_clock(inc.ack_at, tz)}."
    if inc.status == inc_.FALSE_ALARM:
        return f"Closed as a false alarm by {inc.ack_by} at {_clock(inc.ack_at, tz)}."
    if inc.status == inc_.RESOLVED:
        by = (inc.agent_json or {}).get("closed_by") or "Resolved"
        return (f"Cleared by {by}" if by.startswith("the ") else by) + f" at {_clock(inc.closed_at, tz)}."
    return "Not acknowledged yet." if inc.severity == "critical" else ""


def caption(inc: m.Incident, camera_name: str, tz: ZoneInfo, base_url: str) -> str:
    lines = [f"{SEVERITY_WORD.get(inc.severity, inc.severity.title())}, {camera_name}, {_clock(inc.opened_at, tz)}",
             inc.reason]
    why = (inc.agent_json or {}).get("why")
    if why and inc.status in inc_.ACTIVE:
        lines.append(f"Graded {inc.severity}: {why}")
    if inc.count > 1:
        lines.append(f"Seen {inc.count} times, last at {_clock(inc.last_at, tz)}.")
    lines.append(status_line(inc, tz))
    if not _buttons_can_link(base_url):
        lines.append(f"Clip: {incident_link(base_url, inc.id)}")
    return "\n".join(line for line in lines if line)[:CAPTION_MAX]


def buttons(inc: m.Incident, base_url: str) -> list:
    rows = []
    if inc.status == inc_.OPEN:
        rows.append([("Acknowledge", f"ack:{inc.id}"), ("False alarm", f"false:{inc.id}")])
    elif inc.status == inc_.ACKNOWLEDGED:
        rows.append([("False alarm", f"false:{inc.id}")])
    if _buttons_can_link(base_url):
        rows.append([("Open the clip", incident_link(base_url, inc.id))])
    return rows


def reminder(inc: m.Incident, camera_name: str, n: int, t: AlertTuning, tz: ZoneInfo) -> tuple[str, list]:
    """A reminder's text and buttons: while the incident is open it asks again; after, it says who acted."""
    if inc.status == inc_.OPEN:
        first = (inc.reason or "").split("\n")[0]
        minutes = f"{n * t.escalate_min:g}"
        return (f"Still not acknowledged after {minutes} minutes. {camera_name}: {first}",
                [[("Acknowledge", f"ack:{inc.id}"), ("False alarm", f"false:{inc.id}")]])
    return f"{camera_name}: {status_line(inc, tz)}", []


def _phase(inc: m.Incident) -> str:
    """What makes an edit urgent: a new status, verdict or severity (a count change waits out edit_every_s)."""
    return f"{inc.status}|{(inc.agent_json or {}).get('verdict', '')}|{inc.severity}"


def _shown(inc: m.Incident, text: str, rows: list) -> str:
    return f"{_phase(inc)}:{hashlib.sha256(json.dumps([text, rows]).encode()).hexdigest()[:16]}"


def _society(repo) -> tuple[ZoneInfo, list[str], bool]:
    with repo.tx() as s:
        zone = s.scalar(select(m.Workspace.timezone).where(m.Workspace.id == repo.workspace_id)) or "Asia/Kolkata"
        chats = list(s.scalars(select(m.TelegramLink.chat_id).where(m.TelegramLink.workspace_id == repo.workspace_id,
                                                                    m.TelegramLink.chat_id.is_not(None))
                               .order_by(m.TelegramLink.connected_at)))
        meta = s.get(m.Meta, (repo.workspace_id, inc_.WARNINGS_KEY))
    return ZoneInfo(zone), chats, meta is not None and meta.value == "on"


def _camera_name(repo, camera_id: str | None) -> str:
    cam = repo.get_camera(camera_id) if camera_id else None
    return cam.name if cam else "Camera"


def _call(limiter: ChatLimiter, chat: str, now: datetime, fn):
    """Runs one Bot API call to a chat within its limits: (result, None), or (None, seconds to wait)."""
    hold = limiter.hold(chat, now)
    if hold > 0:
        return None, hold
    try:
        result = fn()
    except TelegramError as e:
        if e.retry_after:
            limiter.block(chat, now, e.retry_after)
            return None, e.retry_after
        raise
    limiter.note(chat, now)
    return result, None


def sync_incident(repo, channel, limiter: ChatLimiter, incident_id: str, now: datetime, tuning: AlertTuning,
                  base_url: str, picture=lambda inc: None) -> float | None:
    """Brings the incident's messages in every linked group up to date: posts its message if it has none yet and
    should go to Telegram, and edits the ones whose text or buttons changed (a count change waits out
    edit_every_s; a status change goes at once). Returns the seconds to wait before trying again, or None."""
    with repo.tx() as s:
        inc = s.scalar(select(m.Incident).where(m.Incident.id == incident_id,
                                                m.Incident.workspace_id == repo.workspace_id))
        msgs = [] if inc is None else list(s.scalars(select(m.TelegramMessage).where(
            m.TelegramMessage.workspace_id == repo.workspace_id, m.TelegramMessage.incident_id == incident_id)
            .order_by(m.TelegramMessage.sent_at)))
    if inc is None:
        return None
    tz, chats, warnings = _society(repo)
    camera = _camera_name(repo, inc.camera_id)
    text, rows = caption(inc, camera, tz, base_url), buttons(inc, base_url)
    wanted = inc.status in inc_.ACTIVE and inc_.pushable(inc.severity, warnings)
    if not chats:
        if wanted:
            log.info("Telegram is not connected for %s; alert: %s", repo.workspace_id, text.replace("\n", " | "))
        return None
    waits: list[float] = []
    still: list = []
    for chat in chats:
        mine = [x for x in msgs if x.chat_id == chat]
        main = next((x for x in mine if x.kind == "incident"), None)
        if main is None:
            if not wanted:
                continue
            if not still:
                still.append(picture(inc))
            try:
                mid, wait = _call(limiter, chat, now, lambda: channel.send(chat, text, still[0], rows))
            except TelegramError as e:
                if not e.gone:
                    raise
                log.warning("could not post to chat %s: %s", chat, e.description)
                continue
            if wait:
                waits.append(wait)
                continue
            with repo.tx() as s:
                s.add(m.TelegramMessage(workspace_id=repo.workspace_id, incident_id=inc.id, chat_id=chat,
                                        message_id=mid, kind="incident", photo=bool(still[0]),
                                        shown=_shown(inc, text, rows), sent_at=now))
                row = s.get(m.Incident, inc.id)
                if row is not None and row.telegram_message_id is None:
                    row.telegram_message_id = mid
            continue
        for msg in mine:
            n = sum(1 for x in mine if x.kind == "reminder" and x.sent_at <= msg.sent_at)
            body, keys = (text, rows) if msg.kind == "incident" else reminder(inc, camera, n, tuning, tz)
            want = _shown(inc, body, keys)
            if msg.shown == want:
                continue
            since = (now - (msg.edited_at or msg.sent_at)).total_seconds()
            if msg.shown.rsplit(":", 1)[0] == _phase(inc) and since < tuning.edit_every_s:
                waits.append(tuning.edit_every_s - since)
                continue
            try:
                _, wait = _call(limiter, chat, now, lambda: channel.edit(chat, msg.message_id, body, keys, msg.photo))
            except TelegramError as e:
                if not e.gone:
                    raise
                log.warning("could not edit message %s in chat %s: %s", msg.message_id, chat, e.description)
                wait = None
            if wait:
                waits.append(wait)
                continue
            with repo.tx() as s:
                s.execute(m.TelegramMessage.__table__.update().where(m.TelegramMessage.id == msg.id)
                          .values(shown=want, edited_at=now))
    return min(waits) if waits else None


def repost(repo, channel, limiter: ChatLimiter, incident_id: str, n: int, now: datetime, tuning: AlertTuning
           ) -> tuple[bool, float | None]:
    """Escalation step n: a reminder in reply to the incident's message in each group, while nobody has acted.
    Returns whether the incident is still open, and the seconds to wait when a group is busy."""
    with repo.tx() as s:
        inc = s.scalar(select(m.Incident).where(m.Incident.id == incident_id,
                                                m.Incident.workspace_id == repo.workspace_id))
        msgs = [] if inc is None else list(s.scalars(select(m.TelegramMessage).where(
            m.TelegramMessage.workspace_id == repo.workspace_id, m.TelegramMessage.incident_id == incident_id)))
    if inc is None or inc.status != inc_.OPEN or inc.severity != "critical":
        return False, None
    tz, chats, _ = _society(repo)
    camera = _camera_name(repo, inc.camera_id)
    body, keys = reminder(inc, camera, n, tuning, tz)
    waits = []
    for chat in chats:
        mine = [x for x in msgs if x.chat_id == chat]
        main = next((x for x in mine if x.kind == "incident"), None)
        if main is None or sum(1 for x in mine if x.kind == "reminder") >= n:
            continue
        try:
            mid, wait = _call(limiter, chat, now, lambda: channel.send(chat, body, None, keys, main.message_id))
        except TelegramError as e:
            if not e.gone:
                raise
            log.warning("could not post a reminder to chat %s: %s", chat, e.description)
            continue
        if wait:
            waits.append(wait)
            continue
        with repo.tx() as s:
            s.add(m.TelegramMessage(workspace_id=repo.workspace_id, incident_id=inc.id, chat_id=chat, message_id=mid,
                                    kind="reminder", photo=False, shown=_shown(inc, body, keys), sent_at=now))
    return True, (min(waits) if waits else None)


# the worker's side


_limiter_lock, _faces_lock = threading.Lock(), threading.Lock()


def _limiter(ctx: JobContext) -> ChatLimiter:
    with _limiter_lock:
        return ctx.worker.state.setdefault("telegram_limiter", ChatLimiter(ctx.tuning.alerts))


def _faces(ctx: JobContext, repo):
    """The society's enrolled faces for blurring, read again every Tuning alerts.faces_refresh_s; None when the face
    models cannot be loaded (then no picture leaves the app)."""
    from watchful.components import make_faces
    with _faces_lock:
        cache = ctx.worker.state.setdefault("faces", {})
        held = cache.get(repo.workspace_id)
        if held is not None and time.monotonic() - held[0] < ctx.tuning.alerts.faces_refresh_s:
            return held[1]
        try:
            index = held[1] if held else make_faces(ctx.settings, ctx.tuning)
            index.load(repo.list_enrollments())
        except Exception:
            log.exception("the face models could not be loaded; alerts go without a picture")
            return None
        cache[repo.workspace_id] = (time.monotonic(), index)
        return index


def still_for(ctx: JobContext, repo, inc: m.Incident) -> bytes | None:
    """Telegram's still: cut from the incident's private picture (or its keyframe) with faces of anyone not enrolled
    blurred. None when there is no picture or faces cannot be checked."""
    key = inc.picture_key or inc.keyframe_key
    if not key:
        return None
    try:
        image = cv2.imdecode(np.frombuffer(ctx.worker.blobs.get(key), np.uint8), cv2.IMREAD_COLOR)
    except (OSError, KeyError):
        return None
    faces = _faces(ctx, repo) if image is not None else None
    if faces is None:
        return None
    box = inc.picture_box if inc.picture_key and inc.picture_box else None
    heads = [box] if box and (inc.agent_json or {}).get("cls") == "person" else []
    try:
        return blurred_crop(image, box, faces, heads, ctx.tuning.crops)
    except (cv2.error, ValueError):
        log.exception("the still for %s could not be made", inc.id)
        return None


def _base_url(ctx: JobContext) -> str:
    return ctx.settings.public_base_url or ""


@handler(inc_.SYNC, urgent=True)
def _sync(ctx: JobContext, payload: dict) -> None:
    repo = ctx.repo(payload["workspace_id"])
    wait = sync_incident(repo, ctx.worker.channel, _limiter(ctx), payload["incident_id"], ctx.now, ctx.tuning.alerts,
                         _base_url(ctx), picture=lambda inc: still_for(ctx, repo, inc))
    if wait:
        raise Defer(wait)


@handler(inc_.ESCALATE, urgent=True)
def _escalate(ctx: JobContext, payload: dict) -> None:
    t = ctx.tuning.alerts
    repo, n = ctx.repo(payload["workspace_id"]), int(payload["n"])
    still_open, wait = repost(repo, ctx.worker.channel, _limiter(ctx), payload["incident_id"], n, ctx.now, t)
    if wait:
        raise Defer(wait)
    if still_open and n < t.escalate_times:
        since = datetime.fromisoformat(payload["since"])
        ctx.queue.enqueue(inc_.ESCALATE, {**payload, "n": n + 1},
                          run_after=since + timedelta(minutes=(n + 1) * t.escalate_min),
                          key=f"escalate|{payload['incident_id']}|{n + 1}")


@on_start
def _point_the_webhook(worker) -> None:
    """With a bot token, a webhook secret and a public https address, Telegram is told where to send updates."""
    s = worker.settings
    base = s.public_base_url or ""
    if not (s.telegram_bot_token and s.telegram_webhook_secret and base.startswith("https://")):
        return
    channel = worker.channel
    if isinstance(channel, TelegramChannel):
        channel.bot.set_webhook(f"{base.rstrip('/')}/telegram/webhook/{s.telegram_webhook_secret}",
                                s.telegram_webhook_secret)
