"""Telegram for a society: its linked groups, a one time connect code, whether warnings go too, and the bot's
webhook. Linking and settings need an admin. The webhook (POST /telegram/webhook/{secret}) is Telegram's own: it must
carry TELEGRAM_WEBHOOK_SECRET both in the address and in the X-Telegram-Bot-Api-Secret-Token header, and it is
answered with a Bot API method in the reply (alerts/telegram.py handle_update)."""
import hmac
import threading
import time

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request

from watchful.alerts import incidents as incidents_
from watchful.alerts.telegram import TelegramBot, TelegramError, connect_code, disconnect, handle_update, linked_groups
from watchful.auth.deps import CurrentMember, current_member, require_role

NOT_SET_UP = "Telegram is not set up on this server yet. The operator adds a bot token first."


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    bot = {"lock": threading.Lock()}

    def bot_username() -> str | None:
        """The bot's @name for the instructions, asked of Telegram once."""
        s = ctx.settings
        if not s.telegram_bot_token:
            return None
        with bot["lock"]:
            if "username" not in bot and time.monotonic() - bot.get("tried", -1e9) >= ctx.tuning.alerts.bot_retry_s:
                bot["tried"] = time.monotonic()
                try:
                    me = TelegramBot(s.telegram_bot_token, s.telegram_api_url, ctx.tuning.alerts.http_timeout_s)
                    bot["username"] = me.get_me().get("username")
                except TelegramError:
                    return None
            return bot.get("username")

    def audit(member: CurrentMember, action: str, **data) -> None:
        ctx.db.audit(member.workspace.id, member.user.id, action, data)

    @router.get("/api/telegram")
    def status(member: CurrentMember = Depends(current_member)):
        ready = bool(ctx.settings.telegram_bot_token)
        groups = linked_groups(ctx.db, member.workspace.id)
        return {"ready": ready, "connected": ready and bool(groups), "bot_username": bot_username(),
                "message": None if ready else "Telegram is not connected. Alerts show here in the app.",
                "groups": [{"id": g.id, "title": g.title, "connected_at": g.connected_at} for g in groups],
                "warnings": incidents_.warnings_on(ctx.repo_for(member.workspace.id)),
                # an unacknowledged critical is posted in the group again (alerts/incidents.py _queue_escalation)
                "reminders": {"every_min": ctx.tuning.alerts.escalate_min, "times": ctx.tuning.alerts.escalate_times}}

    @router.post("/api/telegram/connect")
    def connect(member: CurrentMember = Depends(require_role("admin"))):
        if not ctx.settings.telegram_bot_token:
            raise HTTPException(409, NOT_SET_UP)
        code, expires = connect_code(ctx.db, member.workspace.id, tuning=ctx.tuning.alerts)
        audit(member, "telegram.code")
        return {"code": code, "command": f"/connect {code}", "expires_at": expires, "bot_username": bot_username()}

    @router.post("/api/telegram/warnings")
    def warnings(on: bool = Body(..., embed=True), member: CurrentMember = Depends(require_role("admin"))):
        incidents_.set_warnings(ctx.repo_for(member.workspace.id), on)
        audit(member, "telegram.warnings", on=on)
        return {"warnings": on}

    @router.delete("/api/telegram/groups/{link_id}")
    def remove(link_id: str, member: CurrentMember = Depends(require_role("admin"))):
        if not disconnect(ctx.db, member.workspace.id, link_id):
            raise HTTPException(404, "No such group.")
        audit(member, "telegram.disconnect", link=link_id)
        return {"ok": True}

    @router.post("/telegram/webhook/{secret}")
    def webhook(secret: str, request: Request, update: dict = Body(...)):
        expected = ctx.settings.telegram_webhook_secret or ""
        header = request.headers.get("x-telegram-bot-api-secret-token", "")
        want = expected.encode()  # bytes: compare_digest refuses str that is not ASCII
        if not expected or not (hmac.compare_digest(secret.encode(), want)
                                and hmac.compare_digest(header.encode(), want)):
            raise HTTPException(404, "Not found.")
        return handle_update(ctx.db, ctx.repo_for, update) or {}

    return router
