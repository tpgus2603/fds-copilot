"""OpenAICompatibleModelClient (#28): 가짜 OpenAI 호환 서버로 형식 변환·토큰 산정·재시도·루프 통합을 검증합니다."""

import json
from pathlib import Path

import httpx
import pytest

from agent_fakes import FakeModel, high_report, respond, submit
from fds_investigator.agent import model_client as mc
from fds_investigator.agent.loop import PROFILES, Limits, investigate
from fds_investigator.agent.model_client import ModelCallError, ModelRequest
from fds_investigator.agent.openai_client import (OpenAICompatibleModelClient, from_openai_response,
                                                  to_openai_messages, to_openai_tools)
from fds_investigator.agent.tool_session import McpToolSession
from fds_investigator.load_csv import load

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
BASE = "http://llama.local:8080/v1"

TOOLS = [{"name": "get_transaction_history", "description": "이력", "input_schema": {"type": "object"}}]
MESSAGES = [
    {"role": "user", "content": "조사 대상 거래 ID: high"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "조회합니다"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_transaction_history",
         "input": {"target_transaction_id": "high", "lookback_days": 30}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{\"status\": \"ok\"}", "is_error": False}]},
]


def completion(message: dict, finish: str = "tool_calls", prompt: int = 900, completion_tokens: int = 80) -> dict:
    return {"id": "c1", "object": "chat.completion", "model": "local",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens}}


def tool_call(name: str, args, cid: str = "call_1") -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}}


class FakeServer:
    """llama-server 흉내: /v1/chat/completions(스크립트 응답), /apply-template, /tokenize."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": json.dumps(body, ensure_ascii=False)})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": list(range(len(body["content"]) // 4))})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item if isinstance(item, tuple) else (200, item)
        return httpx.Response(status, json=payload)

    def client(self, **kw) -> OpenAICompatibleModelClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return OpenAICompatibleModelClient(BASE, client=http, **kw)

    def chat_bodies(self) -> list[dict]:
        return [b for path, b in self.requests if path == "/v1/chat/completions"]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(mc, "BACKOFF_SECONDS", (0.0, 0.0))


# ---------- 형식 변환 ----------

def test_request_conversion():
    msgs = to_openai_messages("시스템", MESSAGES)
    assert msgs[0] == {"role": "system", "content": "시스템"}
    assert msgs[1] == {"role": "user", "content": "조사 대상 거래 ID: high"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "조회합니다"
    call = msgs[2]["tool_calls"][0]
    assert call["id"] == "toolu_1" and json.loads(call["function"]["arguments"]) == {
        "target_transaction_id": "high", "lookback_days": 30}
    assert msgs[3] == {"role": "tool", "tool_call_id": "toolu_1", "content": "{\"status\": \"ok\"}"}
    assert to_openai_tools(TOOLS) == [{"type": "function", "function": {
        "name": "get_transaction_history", "description": "이력", "parameters": {"type": "object"}}}]


@pytest.mark.parametrize("message,finish,stop", [
    ({"content": None, "tool_calls": [tool_call("x", {})]}, "tool_calls", "tool_use"),
    ({"content": None, "tool_calls": [tool_call("x", {})]}, "stop", "tool_use"),     # 툴 호출이 있으면 tool_use
    ({"content": "끝"}, "stop", "end_turn"),
    ({"content": "잘림"}, "length", "max_tokens"),
    ({"content": ""}, "content_filter", "refusal"),
    ({"content": "?"}, "weird", "unknown"),
])
def test_finish_reason_mapping(message, finish, stop):
    assert from_openai_response(completion(message, finish))[1] == stop


def test_response_conversion_and_reasoning_dropped():
    content, stop, p, c = from_openai_response(completion(
        {"content": "조회", "reasoning_content": "내부 추론...", "tool_calls": [tool_call("get_transaction_history", {"a": 1})]}))
    assert content == [{"type": "text", "text": "조회"},
                       {"type": "tool_use", "id": "call_1", "name": "get_transaction_history", "input": {"a": 1}}]
    assert (stop, p, c) == ("tool_use", 900, 80)
    assert "내부 추론" not in json.dumps(content, ensure_ascii=False)


@pytest.mark.parametrize("raw", ["{not json", "[1,2]", "\"text\""])
def test_invalid_arguments_are_marked_not_dropped(raw):
    content, *_ = from_openai_response(completion({"tool_calls": [tool_call("x", raw)]}))
    assert "__invalid_json__" in content[0]["input"]


def test_missing_tool_call_id_is_generated():
    call = tool_call("x", {})
    del call["id"]
    content, *_ = from_openai_response(completion({"tool_calls": [call]}))
    assert content[0]["id"].startswith("call_")


# ---------- HTTP: 요청 본문, 토큰 산정, 재시도 ----------

async def test_create_sends_thinking_off_and_max_tokens():
    server = FakeServer(completion({"content": "끝"}, "stop"))
    r = await server.client().create(ModelRequest("qwen", "시스템", TOOLS, MESSAGES, 2000), timeout=10)
    body = server.chat_bodies()[0]
    assert server.requests[0][0] == "/v1/chat/completions"
    assert body["max_tokens"] == 2000 and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["tools"][0]["type"] == "function" and body["messages"][0]["role"] == "system"
    assert (r.stop_reason, r.attempts) == ("end_turn", 1)


async def test_thinking_can_be_enabled():
    server = FakeServer(completion({"content": "끝"}, "stop"))
    await server.client(disable_thinking=False).create(ModelRequest("qwen", "s", TOOLS, MESSAGES, 10), timeout=10)
    assert "chat_template_kwargs" not in server.chat_bodies()[0]


async def test_count_tokens_via_server_tokenizer_at_root():
    server = FakeServer()
    n = await server.client().count_tokens(ModelRequest("qwen", "s", TOOLS, MESSAGES, 2000), timeout=10)
    paths = [p for p, _ in server.requests]
    assert paths == ["/apply-template", "/tokenize"]              # /v1 아래가 아니라 서버 루트
    assert "max_tokens" not in server.requests[0][1] and n > 0


async def test_count_tokens_estimate_makes_no_request():
    server = FakeServer()
    n = await server.client(token_count="estimate").count_tokens(ModelRequest("qwen", "s", TOOLS, MESSAGES, 10), 10)
    assert server.requests == [] and n > 0


async def test_retries_on_503_and_connect_error():
    server = FakeServer((503, {"error": "loading model"}), httpx.ConnectError("down"), completion({"content": "끝"}, "stop"))
    r = await server.client().create(ModelRequest("qwen", "s", TOOLS, MESSAGES, 10), timeout=10)
    assert r.attempts == 3


async def test_no_retry_on_400():
    server = FakeServer((400, {"error": "bad tools"}))
    with pytest.raises(ModelCallError) as exc:
        await server.client().create(ModelRequest("qwen", "s", TOOLS, MESSAGES, 10), timeout=10)
    assert exc.value.attempts == 1 and exc.value.retryable is False


# ---------- 루프 통합: 가짜 OpenAI 호환 서버 + 실제 MCP 툴 서버 ----------

@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("openai") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


async def test_full_investigation_through_openai_adapter(db):
    server = FakeServer(
        completion({"content": None, "tool_calls": [tool_call("get_transaction_history",
                                                              {"target_transaction_id": "high", "lookback_days": 7})]}),
        completion({"content": None, "tool_calls": [tool_call("submit_report", high_report(), "call_2")]}),
    )
    r = await investigate("high", server.client(), lambda t: McpToolSession(t, db), "qwen", PROFILES["local"])
    assert r.outcome.status == "reported", r.outcome
    assert set(r.tool_results) == {"EV-TH-high-90d", "EV-TH-high-7d"}
    # 두 번째 요청에 툴 결과가 role=tool 메시지로 전달됨
    second = server.chat_bodies()[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert json.loads(second[-1]["content"])["evidence_id"] == "EV-TH-high-7d"


async def test_invalid_json_arguments_end_in_tool_error_not_crash(db):
    server = FakeServer(
        completion({"content": None, "tool_calls": [tool_call("get_transaction_history", "{broken")]}),
        completion({"content": None, "tool_calls": [tool_call("submit_report", high_report(), "call_2")]}),
    )
    r = await investigate("high", server.client(), lambda t: McpToolSession(t, db), "qwen", PROFILES["local"])
    assert r.outcome.status == "reported"
    first_result = json.loads(server.chat_bodies()[1]["messages"][-1]["content"])
    assert first_result["error"]["code"] == "INVALID_ARGUMENT"     # 툴 서버의 입력 검증이 거부


async def test_context_limit_checked_per_request(db):
    # 다음 요청 15,000 + max_tokens 2,000 > 창 16,384 → 보내지 않음
    r = await investigate("high", FakeModel(respond(submit(high_report())), count=15_000),
                          lambda t: McpToolSession(t, db), "qwen", PROFILES["local"])
    assert r.outcome.reason == "CONTEXT_LIMIT"


def test_profiles():
    assert PROFILES["cloud"] == Limits()
    assert PROFILES["local"].total_seconds > PROFILES["cloud"].total_seconds
    assert PROFILES["local"].context_window == 16_384
