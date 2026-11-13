"""get_transaction_history 요청 처리. 명세 2.1의 검사 순서를 따릅니다.

1. 입력 검증 → 2. 허용 목록 → 3. 대상 거래 존재 → 4. 이력 조회와 집계
"""

import asyncio
import logging
from typing import Any

from . import contracts
from .config import AllowList
from .errors import RETRYABLE, DataSourceUnavailable, ErrorCode
from .history import build_result, split_window, window_start
from .repository import TransactionRepository

log = logging.getLogger(__name__)

MESSAGES = {
    ErrorCode.INVALID_ARGUMENT: "입력이 계약 스키마를 만족하지 않습니다",
    ErrorCode.NOT_FOUND: "대상 거래를 찾을 수 없습니다.",
    ErrorCode.OUT_OF_SCOPE: "대상 거래가 이번 조사 범위에 포함되지 않습니다.",
    ErrorCode.DATA_SOURCE_UNAVAILABLE: "거래 저장소에 연결할 수 없습니다.",
    ErrorCode.TIMEOUT: "거래 이력 조회 시간이 초과되었습니다.",
    ErrorCode.INTERNAL: "내부 오류가 발생했습니다.",
}


def error_result(code: ErrorCode, detail: str | None = None) -> dict:
    message = MESSAGES[code] if detail is None else f"{MESSAGES[code]}: {detail}"
    return {
        "tool": contracts.TOOL_NAME,
        "schema_version": contracts.schema_version(),
        "status": "error",
        "error": {"code": code.value, "message": message, "retryable": code in RETRYABLE},
    }


async def get_transaction_history(
    arguments: dict[str, Any] | None,
    allowlist: AllowList,
    repo: TransactionRepository,
    timeout_seconds: float = 5.0,
) -> dict:
    args = arguments or {}

    # 1. 입력 검증: SDK가 아니라 여기서 계약 스키마로 검증합니다(명세 2.2).
    errors = sorted(contracts.input_validator().iter_errors(args), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        return error_result(ErrorCode.INVALID_ARGUMENT, f"{where}")

    target_id: str = args["target_transaction_id"]
    lookback_days: int = args.get("lookback_days", 90)

    # 2. 허용 목록: 존재 확인보다 먼저 검사해 존재 여부가 노출되지 않게 합니다.
    if not allowlist.permits(target_id):
        return error_result(ErrorCode.OUT_OF_SCOPE)

    try:
        async with asyncio.timeout(timeout_seconds):
            # 3. 대상 거래 존재
            target = await asyncio.to_thread(repo.get_transaction, target_id)
            if target is None:
                return error_result(ErrorCode.NOT_FOUND)
            # 4. 이력 조회: 저장소에는 경계를 포함해 요청하고, 경계 처리는 순수 함수가 강제합니다.
            candidates = await asyncio.to_thread(
                repo.card_history,
                target.card_token,
                window_start(target.trans_at, lookback_days),
                target.trans_at,
            )
    except TimeoutError:
        return error_result(ErrorCode.TIMEOUT)
    except DataSourceUnavailable:
        log.exception("data source unavailable")
        return error_result(ErrorCode.DATA_SOURCE_UNAVAILABLE)
    except Exception:
        log.exception("unexpected error while querying history")
        return error_result(ErrorCode.INTERNAL)

    history, same_second = split_window(target, candidates)
    return build_result(target, history, lookback_days, same_second, contracts.schema_version())
