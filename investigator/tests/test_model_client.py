"""AnthropicModelClient: 실제 SDK를 쓰되 HTTP만 가짜 전송 계층으로 바꿔 검증합니다 (API 키·네트워크 불필요)."""

import json

import anthropic
import httpx2 as httpx  # anthropic SDK 2.x는 httpx 대신 httpx2를 사용합니다
import pytest

from fds_investigator.agent import model_client as mc
from fds_investigator.agent.model_client import AnthropicModelClient, ModelCallError, ModelRequest

pytestmark = pytest.mark.anyio

MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
    "content": [{"type": "text", "text": "조회합니다"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_transaction_history",
                 "input": {"target_transaction_id": "high"}}],
    "stop_reason": "tool_use", "stop_sequence": None,
    "usage": {"input_tokens": 1234, "output_tokens": 56},
}
REQ = ModelRequest("m", "system", [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
                   [{"role": "user", "content": "hi"}], 2000)


def client_with(responses: list[tuple[int, dict]], seen: list[httpx.Request]) -> AnthropicModelClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    sdk = anthropic.AsyncAnthropic(api_key="test-key", max_retries=0,
                                   http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return AnthropicModelClient(client=sdk)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(mc, "BACKOFF_SECONDS", (0.0, 0.0))


async def test_create_normalizes_content_and_usage():
    seen: list[httpx.Request] = []
    r = await client_with([(200, MESSAGE)], seen).create(REQ, timeout=10)
    assert r.stop_reason == "tool_use" and r.attempts == 1
    assert r.content == [{"type": "text", "text": "조회합니다"},
                         {"type": "tool_use", "id": "toolu_1", "name": "get_transaction_history",
                          "input": {"target_transaction_id": "high"}}]
    assert (r.input_tokens, r.output_tokens) == (1234, 56)
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/messages"
    assert body["max_tokens"] == 2000 and body["system"] == "system" and body["tools"][0]["name"] == "t"


async def test_retryable_errors_are_retried_and_counted():
    seen: list[httpx.Request] = []
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    r = await client_with([(529, err), (500, err), (200, MESSAGE)], seen).create(REQ, timeout=10)
    assert r.attempts == 3 and len(seen) == 3


async def test_gives_up_after_max_retries():
    err = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    with pytest.raises(ModelCallError) as exc:
        await client_with([(429, err)] * 3, []).create(REQ, timeout=10)
    assert exc.value.attempts == 3 and exc.value.retryable is True


async def test_client_errors_are_not_retried():
    seen: list[httpx.Request] = []
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    with pytest.raises(ModelCallError) as exc:
        await client_with([(400, err)], seen).create(REQ, timeout=10)
    assert exc.value.attempts == 1 and exc.value.retryable is False and len(seen) == 1


async def test_count_tokens_uses_full_request_without_max_tokens():
    seen: list[httpx.Request] = []
    n = await client_with([(200, {"input_tokens": 4321})], seen).count_tokens(REQ, timeout=10)
    assert n == 4321
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/messages/count_tokens"
    assert set(body) >= {"model", "system", "tools", "messages"} and "max_tokens" not in body
