"""OpenAI 호환 /v1/chat/completions 서버용 ModelClient (#28).

llama.cpp의 llama-server, Ollama, vLLM 등 로컬·내부 추론 서버 대부분이 이 형식을 지원합니다. 조사 루프는
Anthropic 형식(tool_use / tool_result 블록)으로 대화를 관리하므로, 이 어댑터가 요청과 응답을 양방향 변환합니다.

| 조사 루프 (Anthropic 형식)                 | OpenAI 호환 형식                                   |
|-------------------------------------------|---------------------------------------------------|
| system 인자                                | {"role": "system"} 메시지                          |
| tools[].input_schema                      | tools[].function.parameters                        |
| assistant의 tool_use 블록                  | assistant 메시지의 tool_calls (arguments는 JSON 문자열) |
| user의 tool_result 블록                    | {"role": "tool", "tool_call_id": ...} 메시지        |
| stop_reason tool_use/end_turn/max_tokens  | finish_reason tool_calls/stop/length               |
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .model_client import MAX_RETRIES, ModelRequest, ModelResponse, call_with_retry

FINISH_TO_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class OpenAIHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                        httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, OpenAIHTTPError):
        return exc.status in (408, 409, 429) or exc.status >= 500
    return False


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}}
            for t in tools]


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            for block in content:
                if block["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
                elif block["type"] == "text":
                    out.append({"role": "user", "content": block["text"]})
        elif m["role"] == "assistant":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                     for b in blocks if b["type"] == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """arguments는 JSON 문자열이어야 합니다. 깨진 JSON은 버리지 않고 표시해, 툴 서버·검증기가 거부하게 합니다."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"__invalid_json__": str(raw)[:500]}
    return parsed if isinstance(parsed, dict) else {"__invalid_json__": str(raw)[:500]}


def from_openai_response(body: dict[str, Any]) -> tuple[list[dict[str, Any]], str, int, int]:
    choice = body["choices"][0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    # reasoning_content(생각 모드 출력)는 받지 않습니다. 내부 추론은 기록·표시하지 않는 원칙(docs/04 3절)
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {})
        content.append({"type": "tool_use", "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        "name": fn.get("name", ""), "input": _parse_arguments(fn.get("arguments"))})
    has_calls = any(b["type"] == "tool_use" for b in content)
    # 일부 서버는 툴 호출이 있어도 finish_reason을 "stop"으로 돌려줍니다. 툴 호출이 있으면 tool_use로 봅니다.
    stop = "tool_use" if has_calls else FINISH_TO_STOP.get(choice.get("finish_reason") or "", "unknown")
    usage = body.get("usage") or {}
    return content, stop, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


@dataclass
class OpenAICompatibleModelClient:
    """base_url 예: http://localhost:8080/v1 (llama-server), http://localhost:11434/v1 (Ollama)."""

    base_url: str
    disable_thinking: bool = True
    token_count: str = "llama_cpp"        # "llama_cpp": 서버 토크나이저로 산정 / "estimate": 문자 수로 보수적 추정
    api_key: str | None = None            # 내부 추론 서버가 인증을 요구할 때만
    max_retries: int = MAX_RETRIES
    client: httpx.AsyncClient = field(default_factory=httpx.AsyncClient)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _root(self) -> str:
        """llama-server의 /apply-template, /tokenize는 /v1 아래가 아니라 서버 루트에 있습니다."""
        return self.base_url.rstrip("/").removesuffix("/v1")

    def _body(self, request: ModelRequest, with_max_tokens: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": to_openai_messages(request.system, request.messages),
            "tools": to_openai_tools(request.tools),
        }
        if with_max_tokens:
            body["max_tokens"] = request.max_tokens
        if self.disable_thinking:
            # Qwen 계열 채팅 템플릿의 생각 모드 끄기. 템플릿이 모르는 인자는 무시됩니다.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    async def _post(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        resp = await self.client.post(url, json=payload, headers=self._headers(), timeout=timeout)
        if resp.status_code >= 400:
            raise OpenAIHTTPError(resp.status_code, resp.text)
        return resp.json()

    async def count_tokens(self, request: ModelRequest, timeout: float) -> int:
        body = self._body(request, with_max_tokens=False)
        if self.token_count == "estimate":
            # 토큰화 API가 없는 서버용. 한국어·JSON 혼합에서 과소 추정하지 않도록 2자당 1토큰으로 셈합니다.
            return len(json.dumps(body, ensure_ascii=False)) // 2 + 1

        async def call(t: float) -> int:
            prompt = (await self._post(f"{self._root()}/apply-template", body, t))["prompt"]
            return len((await self._post(f"{self._root()}/tokenize", {"content": prompt}, t))["tokens"])

        n, _ = await call_with_retry(call, timeout, self.max_retries, is_retryable)
        return n

    async def create(self, request: ModelRequest, timeout: float) -> ModelResponse:
        body = self._body(request, with_max_tokens=True)

        async def call(t: float) -> dict[str, Any]:
            return await self._post(f"{self.base_url.rstrip('/')}/chat/completions", body, t)

        data, attempts = await call_with_retry(call, timeout, self.max_retries, is_retryable)
        content, stop, prompt_tokens, completion_tokens = from_openai_response(data)
        return ModelResponse(content=content, stop_reason=stop, input_tokens=prompt_tokens,
                             output_tokens=completion_tokens, attempts=attempts)
