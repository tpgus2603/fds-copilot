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

생각(thinking) 모드를 끄는 요청 필드는 서버마다 다릅니다 (THINKING_CONTROLS). 토큰 산정은 llama-server의
/apply-template + /tokenize가 정확하고, 그 밖의 서버는 문자 수 추정(estimate)으로 최선 노력만 합니다.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .model_client import MAX_RETRIES, ModelRequest, ModelResponse, call_with_retry

FINISH_TO_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}
# 툴 호출이 들어 있어도 이 종료 사유가 우선합니다. 잘린 응답의 툴 호출은 인자가 불완전할 수 있고,
# 필터에 걸린 응답의 툴 호출은 실행하면 안 되기 때문입니다 (PR #29 리뷰).
TERMINAL_FINISH = {"length", "content_filter"}

# 생각 모드 끄기 요청 필드. 서버가 모르는 필드는 대부분 무시하지만, 무시되면 생각 출력이 켜진 채로 남습니다.
#   chat_template_kwargs: llama.cpp llama-server, vLLM (Qwen 채팅 템플릿의 enable_thinking)
#   reasoning_effort:     Ollama /v1 (켜고 끄기만 되는 모델에서 "none"은 생각 끔)
#   none:                 아무것도 보내지 않음 (모델 기본값)
THINKING_CONTROLS = ("chat_template_kwargs", "reasoning_effort", "none")
BACKENDS = {  # --backend 값 → (생각 끄기 방식, 기본 토큰 산정 방식)
    "llama_cpp": ("chat_template_kwargs", "llama_cpp"),
    "ollama": ("reasoning_effort", "estimate"),
    "generic": ("none", "estimate"),
}


# 샘플링 프리셋 (#32). server는 아무것도 보내지 않아 서버 기본값을 따릅니다.
# qwen-instruct: Unsloth Qwen3.5 문서의 비사고(instruct) 모드 일반 작업 권장값. presence_penalty는
# 같은 출력의 반복(한 응답에 같은 submit_report를 되풀이하는 현상)을 줄이기 위한 값입니다.
# top_k, min_p는 OpenAI 표준 필드가 아니지만 llama-server, vLLM, Ollama /v1이 받습니다.
SAMPLING_PRESETS: dict[str, dict[str, float | int]] = {
    "server": {},
    "qwen-instruct": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
}


class OpenAIResponseError(Exception):
    """서버가 200을 돌려줬지만 본문이 예상 구조가 아님. 같은 요청을 다시 보내도 고쳐지지 않으므로 재시도하지 않습니다."""


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


def _count(usage: dict[str, Any], key: str) -> int:
    v = usage.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise OpenAIResponseError(f"usage.{key} must be a non-negative integer, got {v!r}")
    return v


def from_openai_response(body: Any) -> tuple[list[dict[str, Any]], str, int, int]:
    """응답 구조를 검증하며 변환합니다. 구조가 어긋나면 OpenAIResponseError (루프에서 MODEL_API_ERROR).

    usage가 없으면 누적 입력 토큰 예산(docs/04 2.4)을 셀 수 없으므로 오류로 봅니다.
    """
    if not isinstance(body, dict):
        raise OpenAIResponseError("response body is not an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise OpenAIResponseError("choices must be a non-empty list of objects")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise OpenAIResponseError("choices[0].message must be an object")
    text, raw_calls = message.get("content"), message.get("tool_calls")
    if text is not None and not isinstance(text, str):
        raise OpenAIResponseError("message.content must be a string or null")
    if raw_calls is not None and not isinstance(raw_calls, list):
        raise OpenAIResponseError("message.tool_calls must be a list or null")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise OpenAIResponseError("usage is missing")
    prompt_tokens, completion_tokens = _count(usage, "prompt_tokens"), _count(usage, "completion_tokens")

    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    # reasoning_content(생각 모드 출력)는 받지 않습니다. 내부 추론은 기록·표시하지 않는 원칙(docs/04 3절)
    # 호출 ID는 응답 안에서 고유해야 합니다. 루프는 결과를 ID로 짝지어 돌려주므로(tool_result.tool_use_id),
    # 같은 ID가 두 번 오면 서로 다른 조회 결과가 한 ID로 덮여 모델이 잘못된 근거를 받습니다(PR #29 재리뷰).
    # 툴을 실행하기 전에 거부합니다. ID가 없거나 빈 문자열이면 기존 호환 동작대로 생성하되, 응답 안에서 고유하게 만듭니다.
    seen: set[str] = set()
    for call in raw_calls or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            raise OpenAIResponseError("tool_calls[].function.name must be a string")
        call_id = call.get("id")
        if call_id is None or call_id == "":
            call_id = f"call_{uuid.uuid4().hex[:12]}"
            while call_id in seen:
                call_id = f"call_{uuid.uuid4().hex[:12]}"
        elif not isinstance(call_id, str):
            raise OpenAIResponseError(f"tool_calls[].id must be a string, got {type(call_id).__name__}")
        elif call_id in seen:
            raise OpenAIResponseError(f"duplicate tool call id in one response: {call_id}")
        seen.add(call_id)
        content.append({"type": "tool_use", "id": call_id,
                        "name": fn["name"], "input": _parse_arguments(fn.get("arguments"))})

    finish = choice.get("finish_reason") or ""
    if finish in TERMINAL_FINISH:
        stop = FINISH_TO_STOP[finish]
    elif any(b["type"] == "tool_use" for b in content):
        # 일부 서버는 툴 호출이 있어도 finish_reason을 "stop"으로 돌려줍니다. 정상 종료일 때만 tool_use로 봅니다.
        stop = "tool_use"
    else:
        stop = FINISH_TO_STOP.get(finish, "unknown")
    return content, stop, prompt_tokens, completion_tokens


@dataclass
class OpenAICompatibleModelClient:
    """base_url 예: http://localhost:8080/v1 (llama-server), http://localhost:11434/v1 (Ollama)."""

    base_url: str
    disable_thinking: bool = True
    thinking_control: str = "chat_template_kwargs"   # THINKING_CONTROLS 중 하나 (BACKENDS 참고)
    sampling: str = "server"                         # SAMPLING_PRESETS 중 하나
    # 한 응답에 툴 호출 하나만. 루프는 한 번에 하나씩 조회하고, 제출은 단독 호출이어야 하므로 병렬이 필요 없습니다.
    parallel_tool_calls: bool = False
    token_count: str = "llama_cpp"        # "llama_cpp": 서버 토크나이저로 산정 / "estimate": 문자 수로 보수적 추정
    api_key: str | None = None            # 내부 추론 서버가 인증을 요구할 때만
    max_retries: int = MAX_RETRIES
    client: httpx.AsyncClient = field(default_factory=httpx.AsyncClient)

    def __post_init__(self) -> None:
        if self.thinking_control not in THINKING_CONTROLS:
            raise ValueError(f"thinking_control must be one of {THINKING_CONTROLS}")
        if self.sampling not in SAMPLING_PRESETS:
            raise ValueError(f"sampling must be one of {sorted(SAMPLING_PRESETS)}")
        if self.token_count not in ("llama_cpp", "estimate"):
            raise ValueError("token_count must be llama_cpp or estimate")

    @classmethod
    def for_backend(cls, backend: str, base_url: str, **kw: Any) -> "OpenAICompatibleModelClient":
        thinking_control, token_count = BACKENDS[backend]
        kw.setdefault("token_count", token_count)
        return cls(base_url, thinking_control=thinking_control, **kw)

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
            # 생성 요청에만 붙입니다. 토큰 산정(/apply-template)에는 영향이 없습니다.
            body.update(SAMPLING_PRESETS[self.sampling])
            body["parallel_tool_calls"] = self.parallel_tool_calls
        if self.disable_thinking:
            if self.thinking_control == "chat_template_kwargs":
                body["chat_template_kwargs"] = {"enable_thinking": False}
            elif self.thinking_control == "reasoning_effort":
                body["reasoning_effort"] = "none"
        return body

    async def _post(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        resp = await self.client.post(url, json=payload, headers=self._headers(), timeout=timeout)
        if resp.status_code >= 400:
            raise OpenAIHTTPError(resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise OpenAIResponseError("response is not JSON") from exc

    async def count_tokens(self, request: ModelRequest, timeout: float) -> int:
        body = self._body(request, with_max_tokens=False)
        if self.token_count == "estimate":
            # 최선 노력 추정: 토큰화 API가 없는 서버용. 한국어·JSON 혼합에서 과소 추정하지 않도록 2자당 1토큰으로
            # 셈하지만, 채팅 템플릿이 붙이는 토큰과 토크나이저 차이는 반영하지 못합니다. 서버의 컨텍스트 길이와
            # --context-window를 같게 맞추고, 서버가 컨텍스트 초과로 거부하면 MODEL_API_ERROR로 끝납니다.
            return len(json.dumps(body, ensure_ascii=False)) // 2 + 1

        async def call(t: float) -> int:
            prompt = (await self._post(f"{self._root()}/apply-template", body, t)).get("prompt")
            if not isinstance(prompt, str):
                raise OpenAIResponseError("/apply-template returned no prompt")
            tokens = (await self._post(f"{self._root()}/tokenize", {"content": prompt}, t)).get("tokens")
            if not isinstance(tokens, list):
                raise OpenAIResponseError("/tokenize returned no tokens")
            return len(tokens)

        n, _ = await call_with_retry(call, timeout, self.max_retries, is_retryable)
        return n

    async def create(self, request: ModelRequest, timeout: float) -> ModelResponse:
        body = self._body(request, with_max_tokens=True)

        async def call(t: float):
            # 구조 검증을 재시도 래퍼 안에서 해서, 실패도 시도 횟수와 함께 ModelCallError로 나가게 합니다.
            return from_openai_response(await self._post(f"{self.base_url.rstrip('/')}/chat/completions", body, t))

        (content, stop, prompt_tokens, completion_tokens), attempts = await call_with_retry(
            call, timeout, self.max_retries, is_retryable)
        return ModelResponse(content=content, stop_reason=stop, input_tokens=prompt_tokens,
                             output_tokens=completion_tokens, attempts=attempts)
