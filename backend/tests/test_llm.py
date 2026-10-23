import base64
import json

import httpx
import pytest

from watchful.agent.llm import AILimitReached, BedrockLLM, FakeLLM, LimitedLLM, Msg, OpenRouterLLM
from watchful.config import Settings
from watchful.store.db import SqliteRepository


def settings(**kw):
    return Settings(demo_password="x", session_secret="y", openrouter_api_key="k", _env_file=None, **kw)


def test_openrouter_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    llm = OpenRouterLLM(settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = llm.complete("be brief", [Msg("user", "look")], images=[b"\xff\xd8jpeg"], json_schema={"type": "object"})
    body = seen["body"]
    assert out == '{"ok": true}' and seen["auth"] == "Bearer k"
    assert body["model"] == "deepseek/deepseek-v4.1-flash" and body["provider"]["sort"] == "throughput"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg").decode()
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}


class StubBedrock:
    def __init__(self):
        self.calls = []

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {"message": {"content": [{"text": "fine"}]}}}


def test_bedrock_request_shape():
    stub = StubBedrock()
    llm = BedrockLLM(settings(bedrock_model="amazon.nova-lite-v1:0"), client=stub)
    assert llm.complete("sys", [Msg("user", "hi")], images=[b"img"]) == "fine"
    kw = stub.calls[0]
    assert kw["modelId"] == "amazon.nova-lite-v1:0" and kw["system"] == [{"text": "sys"}]
    content = kw["messages"][0]["content"]
    assert content[0] == {"text": "hi"} and content[1]["image"]["source"]["bytes"] == b"img"


def test_limiter_blocks_after_cap(tmp_path):
    repo = SqliteRepository(tmp_path / "w.db")
    llm = LimitedLLM(FakeLLM(["a", "b", "c"]), repo, limit=2)
    assert llm.complete("s", [Msg("user", "1")]) == "a"
    assert llm.complete("s", [Msg("user", "2")]) == "b"
    with pytest.raises(AILimitReached):
        llm.complete("s", [Msg("user", "3")])
