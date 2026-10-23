import base64
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from watchful.config import Settings
from watchful.registry import register


class AILimitReached(Exception):
    pass


class AIError(Exception):
    pass


@dataclass
class Msg:
    role: Literal["user", "assistant"]
    text: str


class LLM(Protocol):
    def complete(self, system: str, messages: list[Msg], images: list[bytes] = (), json_schema: dict | None = None,
                 timeout_s: float = 20) -> str: ...


@register("llm", "openrouter")
class OpenRouterLLM:
    def __init__(self, settings: Settings, client: httpx.Client | None = None, model: str | None = None,
                 name: str = "openrouter"):
        self.s = settings
        self.model = model or settings.openrouter_model
        self.client = client or httpx.Client()

    def complete(self, system, messages, images=(), json_schema=None, timeout_s=20) -> str:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for i, m in enumerate(messages):
            if i == len(messages) - 1 and m.role == "user" and images:
                parts = [{"type": "text", "text": m.text}]
                parts += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
                          for b in images]
                msgs.append({"role": "user", "content": parts})
            else:
                msgs.append({"role": m.role, "content": m.text})
        body = {"model": self.model, "messages": msgs, "temperature": 0,
                "max_tokens": self.s.ai_max_tokens, "provider": {"sort": "throughput"},
                "reasoning": {"effort": "none"}}
        if json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "strict": False, "schema": json_schema}}
        try:
            r = self.client.post(f"{self.s.openrouter_base_url}/chat/completions", json=body, timeout=timeout_s,
                                 headers={"authorization": f"Bearer {self.s.openrouter_api_key}"})
        except httpx.TimeoutException as e:
            raise TimeoutError("the AI took too long") from e
        if r.status_code >= 400:
            raise AIError(f"AI provider answered {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise AIError("unexpected AI response") from e


@register("llm", "bedrock")
class BedrockLLM:
    def __init__(self, settings: Settings, client=None, model: str | None = None, name: str = "bedrock"):
        self.s = settings
        self.model = model or settings.bedrock_model
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-runtime", region_name=settings.aws_region,
                                  config=Config(read_timeout=30, retries={"max_attempts": 1}))
        self.client = client

    def complete(self, system, messages, images=(), json_schema=None, timeout_s=20) -> str:
        msgs = []
        for i, m in enumerate(messages):
            content: list[dict] = [{"text": m.text}]
            if i == len(messages) - 1 and m.role == "user":
                content += [{"image": {"format": "jpeg", "source": {"bytes": b}}} for b in images]
            msgs.append({"role": m.role, "content": content})
        if json_schema:
            system += "\nReply with one JSON object only."
        r = self.client.converse(modelId=self.model, system=[{"text": system}], messages=msgs,
                                 inferenceConfig={"maxTokens": self.s.ai_max_tokens, "temperature": 0})
        return "".join(p.get("text", "") for p in r["output"]["message"]["content"])


class FakeLLM:
    """Scripted replies for tests; an Exception in the script is raised instead."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[tuple] = []

    def complete(self, system, messages, images=(), json_schema=None, timeout_s=20) -> str:
        self.calls.append((system, messages, list(images), json_schema))
        if not self.script:
            raise AIError("no scripted reply left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class LimitedLLM:
    """Counts every call against the daily cap."""

    def __init__(self, inner: LLM, repo, limit: int):
        self.inner, self.repo, self.limit = inner, repo, limit

    def complete(self, system, messages, images=(), json_schema=None, timeout_s=20) -> str:
        if self.repo.count_ai_calls_today() >= self.limit:
            raise AILimitReached("daily AI limit reached")
        self.repo.record_ai_call()
        return self.inner.complete(system, messages, images, json_schema, timeout_s)

