"""모델 호출 인터페이스와 Anthropic 구현.

조사 루프는 ModelClient 인터페이스에만 의존합니다. 외부 API와 내부 모델, 테스트용 가짜 클라이언트를
같은 자리에 바꿔 끼울 수 있게 하기 위해서입니다(금융 망분리 환경의 모델 교체 대비).

재시도는 SDK 자동 재시도(max_retries)를 끄고 여기서 직접 합니다. 그래야 논리 호출과 실제 시도 횟수를
구분해 기록할 수 있습니다(docs/04 2.4).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

MAX_RETRIES = 2          # 원래 1회 + 재시도 2회 = 최대 3회 시도
BACKOFF_SECONDS = (1.0, 2.0)


@dataclass(frozen=True)
class ModelResponse:
    content: list[dict[str, Any]]      # {"type":"text","text":...} | {"type":"tool_use","id","name","input"}
    stop_reason: str
    input_tokens: int
    output_tokens: int
    attempts: int = 1


@dataclass
class ModelRequest:
    model: str
    system: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    max_tokens: int

    def counting_params(self) -> dict[str, Any]:
        return {"model": self.model, "system": self.system, "tools": self.tools, "messages": self.messages}

    def create_params(self) -> dict[str, Any]:
        return {**self.counting_params(), "max_tokens": self.max_tokens}


class ModelCallError(Exception):
    """재시도 후에도 실패한 모델 호출. attempts에 실제 시도 횟수를 담습니다."""

    def __init__(self, message: str, attempts: int, retryable: bool):
        super().__init__(message)
        self.attempts = attempts
        self.retryable = retryable


class ModelClient(Protocol):
    async def count_tokens(self, request: ModelRequest, timeout: float) -> int: ...

    async def create(self, request: ModelRequest, timeout: float) -> ModelResponse: ...


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.APIConnectionError):   # 연결 실패, 타임아웃 포함
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (408, 409, 429) or exc.status_code >= 500
    return False


@dataclass
class AnthropicModelClient:
    client: anthropic.AsyncAnthropic = field(default_factory=lambda: anthropic.AsyncAnthropic(max_retries=0))
    max_retries: int = MAX_RETRIES

    async def _with_retry(self, call, timeout: float):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        attempts = 0
        while True:
            attempts += 1
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ModelCallError("deadline exceeded before model call", attempts - 1, True)
            try:
                return await call(remaining), attempts
            except Exception as exc:  # noqa: BLE001 - SDK 예외를 분류해 다시 던집니다
                retryable = is_retryable(exc)
                if not retryable or attempts > self.max_retries:
                    raise ModelCallError(type(exc).__name__, attempts, retryable) from exc
                wait = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
                if loop.time() + wait >= deadline:
                    raise ModelCallError(type(exc).__name__, attempts, retryable) from exc
                await asyncio.sleep(wait)

    async def count_tokens(self, request: ModelRequest, timeout: float) -> int:
        async def call(t):
            return await self.client.messages.count_tokens(**request.counting_params(), timeout=t)
        result, _ = await self._with_retry(call, timeout)
        return result.input_tokens

    async def create(self, request: ModelRequest, timeout: float) -> ModelResponse:
        async def call(t):
            return await self.client.messages.create(**request.create_params(), timeout=t)
        msg, attempts = await self._with_retry(call, timeout)
        content = []
        for block in msg.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return ModelResponse(
            content=content,
            stop_reason=msg.stop_reason or "unknown",
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            attempts=attempts,
        )
