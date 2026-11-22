"""get_merchant_profile의 계산 규칙 (명세 7절, #56). 순수 함수이며 I/O가 없습니다.

라벨(is_fraud)을 쓰지 않는 이용 집계입니다. 가맹점 통계는 **다른 카드**의 거래로만 계산해, 가맹점의 평소 모습과
이 카드의 이 가맹점 이용 이력을 나눕니다. 다른 카드가 MIN_CARDS장 미만이면 금액 통계를 내지 않습니다. 카드 한두
장의 평균 금액은 그 사람들의 결제 금액 자체이기 때문입니다(최소 익명성, k-익명성과 같은 원리).
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from .domain import Transaction
from .history import CENT, MIN_SAMPLE, median, money, ts, window_start

MIN_CARDS = 3
DEFAULT_LOOKBACK_DAYS = 90


def split_merchant_window(
    target: Transaction, candidates: list[Transaction], start: datetime
) -> tuple[list[Transaction], list[Transaction]]:
    """[start, as_of) 안의 같은 가맹점 거래를 (다른 카드, 이 카드)로 나눕니다. 같은 초와 대상 자신은 제외합니다.

    저장소가 범위를 벗어난 행을 돌려주더라도 여기서 다시 강제합니다(거래 이력 툴과 같은 원칙).
    """
    others: list[Transaction] = []
    own: list[Transaction] = []
    for t in candidates:
        if t.merchant_name != target.merchant_name or t.transaction_id == target.transaction_id:
            continue
        if not (start <= t.trans_at < target.trans_at):
            continue
        (own if t.card_token == target.card_token else others).append(t)
    return others, own


def build_merchant_result(target: Transaction, others: list[Transaction], own: list[Transaction],
                          lookback_days: int, schema_version: str) -> dict:
    n, cards = len(others), len({t.card_token for t in others})
    stats: dict = {"txn_count": n, "card_count": cards, "amount_mean": None, "amount_median": None,
                   "amount_max": None, "target_to_mean_ratio": None, "target_exceeds_max": None}
    if n == 0:
        status = "empty"
    elif n < MIN_SAMPLE or cards < MIN_CARDS:
        status = "insufficient"
    else:
        status = "ok"
        amounts = sorted(t.amount for t in others)
        raw_mean = sum(amounts, Decimal(0)) / n
        amount_max = amounts[-1]
        stats.update(
            amount_mean=money(raw_mean),
            amount_median=money(median(amounts)),
            amount_max=money(amount_max),
            # 비율 분모는 반올림 전 평균 (거래 이력 툴과 같은 규칙). 금액이 모두 0이면 정의되지 않음
            target_to_mean_ratio=None if raw_mean == 0
            else float((target.amount / raw_mean).quantize(CENT, rounding=ROUND_HALF_UP)),
            target_exceeds_max=target.amount > amount_max,
        )
    last = max(own, key=lambda t: t.trans_at, default=None)
    return {
        "tool": "get_merchant_profile",
        "schema_version": schema_version,
        "status": status,
        "evidence_id": f"EV-MP-{target.transaction_id}-{lookback_days}d",
        "basis": {"as_of": ts(target.trans_at), "window_start": ts(window_start(target.trans_at, lookback_days)),
                  "lookback_days": lookback_days, "min_sample": MIN_SAMPLE, "min_cards": MIN_CARDS},
        "merchant": {"merchant_name": target.merchant_name, "category": target.category},
        "target": {"transaction_id": target.transaction_id, "amount": money(target.amount)},
        "merchant_stats": stats,
        "card_at_merchant": {"seen_before": bool(own), "count": len(own),
                             "last_seen_at": None if last is None else ts(last.trans_at)},
    }
