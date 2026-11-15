"""조사 루프 테스트용 가짜 모델 클라이언트와 응답 생성 도우미."""

import asyncio
import copy
import itertools
from typing import Any

from fds_investigator.agent.model_client import ModelCallError, ModelRequest, ModelResponse

_ids = itertools.count(1)


def tool_use(name: str, **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": name, "input": inp}


def history(target: str, days: int | None = None) -> dict[str, Any]:
    args = {"target_transaction_id": target} | ({"lookback_days": days} if days is not None else {})
    return tool_use("get_transaction_history", **args)


def submit(report: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": "submit_report", "input": report}


def respond(*blocks: dict[str, Any], stop: str = "tool_use", tokens: int = 1000) -> ModelResponse:
    return ModelResponse(content=list(blocks), stop_reason=stop, input_tokens=tokens, output_tokens=100)


def text(t: str, stop: str = "end_turn") -> ModelResponse:
    return respond({"type": "text", "text": t}, stop=stop)


HIGH_REPORT = {
    "summary": "평소 소액 결제가 이어지던 카드에서 비교 기간에 이용한 적 없는 업종으로 평균을 크게 넘는 금액이 결제되었습니다.",
    "findings": [
        {"kind": "suspicion", "statement": "대상 금액이 {0}이며 {1}입니다.",
         "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/target_to_mean_ratio", "value": 10.0},
                      {"evidence_id": "EV-TH-high-90d", "path": "/stats/target_exceeds_max", "value": True}]},
        {"kind": "suspicion", "statement": "대상 거래 업종은 {0}입니다.",
         "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/target_category/seen_before", "value": False}]},
        {"kind": "gap", "statement": "비교 표본이 {0}으로 최소 기준만 충족합니다.",
         "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/txn_count", "value": 5}]},
    ],
    "recommendation": "further_review_recommended",
}


def high_report(**changes: Any) -> dict[str, Any]:
    r = copy.deepcopy(HIGH_REPORT)
    r.update(changes)
    return r


class FakeModel:
    """script 항목: ModelResponse | Exception | callable(request) -> ModelResponse."""

    def __init__(self, *script: Any, count: Any = 1000, delay: float = 0.0):
        self.script = list(script)
        self.count = count
        self.delay = delay
        self.requests: list[ModelRequest] = []

    async def count_tokens(self, request: ModelRequest, timeout: float) -> int:
        if isinstance(self.count, Exception):
            raise self.count
        return self.count(request) if callable(self.count) else self.count

    async def create(self, request: ModelRequest, timeout: float) -> ModelResponse:
        self.requests.append(copy.deepcopy(request))
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.script:
            raise AssertionError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item


def api_error(attempts: int = 3) -> ModelCallError:
    return ModelCallError("InternalServerError", attempts, True)
