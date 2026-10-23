import logging
import time
from typing import Protocol

log = logging.getLogger("watchful.notify")


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...


class LogNotifier:
    def send(self, subject: str, body: str) -> None:
        log.info("notify: %s | %s", subject, body)


class SnsNotifier:
    def __init__(self, topic_arn: str, region: str, client=None):
        if client is None:
            import boto3
            client = boto3.client("sns", region_name=region)
        self.topic_arn, self.client = topic_arn, client

    def send(self, subject: str, body: str) -> None:
        self.client.publish(TopicArn=self.topic_arn, Subject=subject[:100], Message=body)


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


def make_notifier(settings) -> Notifier:
    if settings.notify == "sns" and settings.sns_topic_arn:
        return SnsNotifier(settings.sns_topic_arn, settings.aws_region)
    return LogNotifier()
