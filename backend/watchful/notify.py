import logging
import secrets
import time
from typing import Protocol

from watchful.registry import register

log = logging.getLogger("watchful.notify")


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...


@register("notify", "log")
class LogNotifier:
    def __init__(self, name: str = "log", **_):
        pass

    def send(self, subject: str, body: str) -> None:
        log.info("notify: %s | %s", subject, body)


@register("notify", "sns")
class SnsNotifier:
    def __init__(self, topic_arn: str, region: str, client=None, name: str = "sns"):
        if client is None:
            import boto3
            client = boto3.client("sns", region_name=region)
        self.topic_arn, self.client = topic_arn, client

    def send(self, subject: str, body: str) -> None:
        self.client.publish(TopicArn=self.topic_arn, Subject=subject[:100], Message=body)


class AlertChannel(Protocol):
    """Where incidents go: Telegram (alerts/telegram.py) or the log that stands in without a bot token. `buttons` are
    rows of (text, action) pairs; an action is "ack:<incident id>", "false:<incident id>" or a link."""
    live: bool  # reaches people

    def send(self, chat_id: str, text: str, photo: bytes | None = None, buttons: list | None = None,
             reply_to: str | None = None) -> str: ...
    def edit(self, chat_id: str, message_id: str, text: str, buttons: list | None = None,
             photo: bool = False) -> None: ...


@register("alerts", "log")
class LogChannel:
    """Stands in for Telegram when no bot token is set: each message is logged, and the app says Telegram is not
    connected."""
    live = False

    def __init__(self, name: str = "log", **_):
        pass

    def send(self, chat_id: str, text: str, photo: bytes | None = None, buttons: list | None = None,
             reply_to: str | None = None) -> str:
        log.info("alert to %s: %s", chat_id, text.replace("\n", " | "))
        return f"log_{secrets.token_hex(8)}"

    def edit(self, chat_id: str, message_id: str, text: str, buttons: list | None = None,
             photo: bool = False) -> None:
        log.info("alert %s in %s now reads: %s", message_id, chat_id, text.replace("\n", " | "))


class ApprovalNotifier:
    """Sends one message when a person approves a serious alert, at most once per min_gap_s."""

    def __init__(self, notifier: Notifier, min_gap_s: float = 600):
        self.notifier, self.min_gap_s = notifier, min_gap_s
        self._last: float | None = None

    def approve(self, alert, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self._last is not None and now - self._last < self.min_gap_s:
            return False
        self._last = now
        self.notifier.send(f"Watchful: {alert.summary[:80]}", f"{alert.summary}\nCamera: {alert.camera_id}")
        return True

