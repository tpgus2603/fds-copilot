"""조사 루프 (docs/04 2장, 5장, 6장).

판단(어떤 툴을 어떤 인자로 부를지, 무엇을 보고할지)은 모델이 하고, 실행 권한·범위·횟수·시간·검증은
이 코드가 강제합니다.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .model_client import ModelCallError, ModelClient, ModelRequest, ModelResponse
from .prompts import NUDGE, SUBMIT_REPORT, SYSTEM_PROMPT, initial_user_message, submit_report_tool
from .render import render_statement
from .tool_session import ToolCallResult, ToolSession
from .verify import verify_report

HISTORY_TOOL = "get_transaction_history"
BASELINE_LOOKBACK = 90
TOOL_RETRY_WAIT = 1.0
TOKEN_MARGIN = 1.05


@dataclass(frozen=True)
class Limits:
    max_model_calls: int = 6
    max_tool_calls: int = 8
    tool_timeout: float = 10.0
    total_seconds: float = 120.0
    max_tokens: int = 2000
    input_token_budget: int = 60_000


@dataclass
class Usage:
    model_calls: int = 0
    model_attempts: int = 0
    tool_calls: int = 0           # 실행을 허가한 조회 수 (기본 증거·제출 제외)
    tool_attempts: int = 0
    rejected_calls: int = 0
    input_tokens: int = 0         # spent: 성공한 요청의 usage.input_tokens 합
    output_tokens: int = 0


@dataclass
class Outcome:
    status: str                   # "reported" | "incomplete"
    reason: str | None = None
    detail: Any = None


@dataclass
class InvestigationResult:
    run_id: str
    target_transaction_id: str
    model: str
    limits: Limits
    steps: list[dict[str, Any]]
    tool_results: dict[str, dict]
    usage: Usage
    outcome: Outcome
    report: dict | None = None
    rendered: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_run_id() -> str:
    """시간순 정렬이 가능한 ULID 형식 실행 ID."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "run_" + "".join(alphabet[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _canonical(name: str, args: dict[str, Any]) -> str:
    return name + json.dumps(args, sort_keys=True, ensure_ascii=False)


def _tool_result_block(tool_use_id: str, payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": json.dumps(payload, ensure_ascii=False), "is_error": is_error}


def _rejection(code: str, message: str) -> dict[str, Any]:
    return {"status": "rejected", "error": {"code": code, "message": message}}


class _Stop(Exception):
    def __init__(self, reason: str, detail: Any = None):
        self.reason, self.detail = reason, detail


async def investigate(
    target_transaction_id: str,
    model_client: ModelClient,
    open_tools: Callable[[str], AbstractAsyncContextManager[ToolSession]],
    model: str,
    limits: Limits = Limits(),
) -> InvestigationResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limits.total_seconds          # 단일 마감 시각 (2.4)
    steps: list[dict[str, Any]] = []
    snapshots: dict[str, dict] = {}
    usage = Usage()
    result = InvestigationResult(new_run_id(), target_transaction_id, model, limits, steps, snapshots, usage,
                                 Outcome("incomplete"))

    def remaining(cap: float | None = None) -> float:
        left = deadline - loop.time()
        if left <= 0:
            raise _Stop("TIME_LIMIT")
        return min(cap, left) if cap is not None else left

    def record(kind: str, started: float, **data: Any) -> None:
        steps.append({"type": kind, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                      "duration_ms": round((loop.time() - started) * 1000), **data})

    async def run_tool(session: ToolSession, name: str, args: dict[str, Any], kind: str) -> ToolCallResult:
        """툴 실행. retryable 오류면 조사 서비스가 1회 재시도합니다(2.5)."""
        for attempt in (1, 2):
            started = loop.time()
            usage.tool_attempts += 1
            try:
                res = await asyncio.wait_for(session.call(name, args), remaining(limits.tool_timeout))
            except TimeoutError:
                res = ToolCallResult({"status": "error", "error": {"code": "TIMEOUT", "message": "툴 호출 시간 초과",
                                                                   "retryable": True}}, True)
            record(kind, started, tool=name, arguments=args, attempt=attempt,
                   status=res.payload.get("status"), evidence_id=res.payload.get("evidence_id"),
                   error=res.payload.get("error"))
            if "evidence_id" in res.payload:
                snapshots[res.payload["evidence_id"]] = res.payload
            retryable = res.is_error and res.payload.get("error", {}).get("retryable") is True
            if not retryable or attempt == 2:
                return res
            await asyncio.sleep(min(TOOL_RETRY_WAIT, remaining()))
        raise AssertionError("unreachable")

    async def run_loop(session: ToolSession) -> None:
        # 1. 기본 증거: 코드가 결정적으로 수집 (2.2). empty·insufficient는 정상 진행, error만 중단.
        baseline_args = {"target_transaction_id": target_transaction_id, "lookback_days": BASELINE_LOOKBACK}
        baseline = await run_tool(session, HISTORY_TOOL, baseline_args, "baseline_tool_call")
        if baseline.is_error or baseline.payload.get("status") == "error":
            raise _Stop("BASELINE_UNAVAILABLE", baseline.payload.get("error"))
        executed = {_canonical(HISTORY_TOOL, baseline_args): baseline.payload.get("evidence_id")}

        lookup_names = {t["name"] for t in session.tools}
        tools = [*session.tools, submit_report_tool()]
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": initial_user_message(target_transaction_id, baseline.payload)}]
        nudged = False

        while True:
            # 2. 모델 호출 상한 (논리 호출)
            if usage.model_calls >= limits.max_model_calls:
                raise _Stop("MAX_MODEL_CALLS")
            request = ModelRequest(model, SYSTEM_PROMPT, tools, messages, limits.max_tokens)

            # 3. 입력 토큰 상한: 요청 전에 spent + count_tokens(다음 요청 전체) x 1.05 <= 예산 (2.4)
            started = loop.time()
            try:
                budget = remaining()
                next_tokens = await asyncio.wait_for(model_client.count_tokens(request, budget), budget)
            except _Stop:
                raise
            except TimeoutError as exc:
                raise _Stop("TIME_LIMIT", "count_tokens") from exc
            except ModelCallError as exc:
                if loop.time() >= deadline:
                    raise _Stop("TIME_LIMIT", "count_tokens") from exc
                raise _Stop("MODEL_API_ERROR", {"stage": "count_tokens", "error": str(exc)}) from exc
            projected = usage.input_tokens + next_tokens * TOKEN_MARGIN
            record("token_check", started, spent=usage.input_tokens, next=next_tokens, projected=round(projected))
            if projected > limits.input_token_budget:
                raise _Stop("TOKEN_BUDGET", {"spent": usage.input_tokens, "next": next_tokens})

            # 4. 모델 호출
            started = loop.time()
            try:
                budget = remaining()
                response: ModelResponse = await asyncio.wait_for(model_client.create(request, budget), budget)
            except _Stop:
                raise
            except TimeoutError as exc:
                raise _Stop("TIME_LIMIT", "create") from exc
            except ModelCallError as exc:
                usage.model_attempts += exc.attempts
                if loop.time() >= deadline:
                    raise _Stop("TIME_LIMIT", "create") from exc
                raise _Stop("MODEL_API_ERROR", {"stage": "create", "error": str(exc), "attempts": exc.attempts}) from exc
            usage.model_calls += 1
            usage.model_attempts += response.attempts
            usage.input_tokens += response.input_tokens
            usage.output_tokens += response.output_tokens
            tool_uses = [b for b in response.content if b["type"] == "tool_use"]
            record("model_response", started, stop_reason=response.stop_reason, attempts=response.attempts,
                   input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                   tool_uses=[{"name": b["name"], "input": b["input"]} for b in tool_uses],
                   text=[b["text"] for b in response.content if b["type"] == "text"])  # 감사용, 화면 비표시
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "max_tokens":
                raise _Stop("MODEL_OUTPUT_TRUNCATED")
            if response.stop_reason == "refusal":
                raise _Stop("MODEL_REFUSAL")
            if response.stop_reason == "end_turn" and not tool_uses:
                if nudged:
                    raise _Stop("NO_REPORT")
                nudged = True
                messages.append({"role": "user", "content": NUDGE})
                continue
            if response.stop_reason != "tool_use":
                raise _Stop("MODEL_UNEXPECTED_STOP", response.stop_reason)

            submits = [b for b in tool_uses if b["name"] == SUBMIT_REPORT]
            lookups = [b for b in tool_uses if b["name"] != SUBMIT_REPORT]

            # 5. 제출 규칙: submit_report는 그 응답의 유일한 툴 호출 (2.6)
            if len(submits) == 1 and not lookups:
                report = submits[0]["input"]
                result.report = report
                record("report_submitted", loop.time())
                verdict = verify_report(report, snapshots)
                record("verification", loop.time(), ok=verdict.ok, code=verdict.code, failures=verdict.failures)
                if not verdict.ok:
                    raise _Stop(verdict.code, verdict.failures)
                result.rendered = {
                    "summary": report["summary"],
                    "findings": [{"kind": f["kind"], "text": render_statement(f["statement"], f["evidence"]),
                                  "evidence": [f"{result.run_id}/{e['evidence_id']}#{e['path']}" for e in f["evidence"]]}
                                 for f in report["findings"]],
                    "recommendation": report["recommendation"],
                }
                result.outcome = Outcome("reported")
                return

            results: dict[str, dict[str, Any]] = {}
            if submits:
                code = "MIXED_WITH_TOOLS" if lookups else "DUPLICATE_SUBMIT"
                for b in submits:
                    usage.rejected_calls += 1
                    results[b["id"]] = _tool_result_block(b["id"], _rejection(
                        code, "submit_report는 다른 툴 없이 단독으로 다시 제출하십시오."), True)
                record("submit_rejected", loop.time(), code=code, count=len(submits),
                       inputs=[b["input"] for b in submits])

            # 6. 조회 툴 처리 (2.5): 미등록·중복은 실행하지 않음, 남은 한도를 넘는 배치는 전체 거부 (2.4)
            candidates = []
            for b in lookups:
                key = _canonical(b["name"], b["input"])
                if b["name"] not in lookup_names:
                    usage.rejected_calls += 1
                    results[b["id"]] = _tool_result_block(b["id"], _rejection("UNKNOWN_TOOL", f"{b['name']}은 없는 툴입니다."), True)
                elif key in executed:
                    usage.rejected_calls += 1
                    results[b["id"]] = _tool_result_block(b["id"], _rejection(
                        "DUPLICATE_CALL", f"같은 조회를 이미 실행했습니다. evidence_id={executed[key]}"), True)
                else:
                    candidates.append(b)
            left = limits.max_tool_calls - usage.tool_calls
            if len(candidates) > left:
                for b in candidates:
                    usage.rejected_calls += 1
                    results[b["id"]] = _tool_result_block(b["id"], _rejection(
                        "LIMIT_EXCEEDED", f"조회 한도를 넘습니다. 남은 조회는 {left}회입니다."), True)
                record("lookups_rejected", loop.time(), code="LIMIT_EXCEEDED", requested=len(candidates), remaining=left)
                candidates = []
            for b in candidates:
                usage.tool_calls += 1
                res = await run_tool(session, b["name"], b["input"], "tool_call")
                if "evidence_id" in res.payload:
                    executed[_canonical(b["name"], b["input"])] = res.payload["evidence_id"]
                results[b["id"]] = _tool_result_block(b["id"], res.payload, res.is_error)

            # 모든 tool_use에 대해, 요청 순서대로 tool_result를 돌려줍니다 (API 규칙)
            messages.append({"role": "user", "content": [results[b["id"]] for b in tool_uses]})

    try:
        started = loop.time()
        session_cm = open_tools(target_transaction_id)
        try:
            session = await asyncio.wait_for(session_cm.__aenter__(), remaining())
        except _Stop:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _Stop("TOOL_SERVER_FAILURE", type(exc).__name__) from exc
        record("tool_server_started", started, tools=[t["name"] for t in session.tools])
        try:
            await run_loop(session)
        finally:
            await session_cm.__aexit__(None, None, None)
    except _Stop as stop:
        result.outcome = Outcome("incomplete", stop.reason, stop.detail)
    return result
