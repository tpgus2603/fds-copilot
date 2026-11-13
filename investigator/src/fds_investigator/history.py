"""get_transaction_history의 집계 규칙 (명세 3.4, 3.6). 순수 함수이며 I/O가 없습니다."""

import math
from collections import Counter
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .domain import Transaction

CENT = Decimal("0.01")
SHARE_Q = Decimal("0.0001")
MIN_SAMPLE = 5          # 1주차 운영 기준 (명세 3.7)
P95_MIN_SAMPLE = 20
TOP_CATEGORIES = 10
RECENT_LIMIT = 10
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def ts(value: datetime) -> str:
    return value.strftime(TS_FORMAT)


def window_start(as_of: datetime, lookback_days: int) -> datetime:
    return as_of - timedelta(days=lookback_days)


def split_window(
    target: Transaction, candidates: list[Transaction], start: datetime
) -> tuple[list[Transaction], int]:
    """비교 대상 [start, as_of)와, 대상과 같은 초에 있어 제외된 건수를 나눕니다.

    같은 카드만 포함합니다. 저장소가 범위를 벗어난 행을 돌려주더라도 여기서 다시 강제합니다
    (다른 카드, 대상 자체, 하한 이전, 같은 초, 이후 거래 제외).
    """
    as_of = target.trans_at
    kept: list[Transaction] = []
    same_second = 0
    for t in candidates:
        if t.card_token != target.card_token or t.transaction_id == target.transaction_id:
            continue
        if t.trans_at < start:
            continue
        if t.trans_at < as_of:
            kept.append(t)
        elif t.trans_at == as_of:
            same_second += 1
    return kept, same_second


def median(sorted_amounts: list[Decimal]) -> Decimal:
    n = len(sorted_amounts)
    mid = n // 2
    if n % 2:
        return sorted_amounts[mid]
    return (sorted_amounts[mid - 1] + sorted_amounts[mid]) / 2


def p95_nearest_rank(sorted_amounts: list[Decimal]) -> Decimal:
    rank = math.ceil(Decimal("0.95") * len(sorted_amounts))
    return sorted_amounts[rank - 1]


def build_result(
    target: Transaction,
    history: list[Transaction],
    lookback_days: int,
    same_second_excluded: int,
    schema_version: str,
) -> dict:
    n = len(history)
    notes: list[str] = []
    if same_second_excluded:
        notes.append(f"기준 시점과 같은 시각의 거래 {same_second_excluded}건을 비교 집계에서 제외했습니다.")

    if n == 0:
        status = "empty"
    elif n < MIN_SAMPLE:
        status = "insufficient"
    else:
        status = "ok"

    if n == 0:
        stats = {
            "txn_count": 0,
            "amount_mean": None,
            "amount_median": None,
            "amount_p95": None,
            "amount_max": None,
            "target_to_mean_ratio": None,
            "target_exceeds_max": None,
        }
    else:
        amounts = sorted(t.amount for t in history)
        raw_mean = sum(amounts, Decimal(0)) / n
        amount_max = amounts[-1]
        if raw_mean == 0:
            ratio = None
            notes.append("비교 기간 평균 금액이 0이어서 평균 대비 비율을 계산하지 않았습니다.")
        else:
            ratio = float((target.amount / raw_mean).quantize(CENT, rounding=ROUND_HALF_UP))
        stats = {
            "txn_count": n,
            "amount_mean": money(raw_mean),
            "amount_median": money(median(amounts)),
            "amount_p95": money(p95_nearest_rank(amounts)) if n >= P95_MIN_SAMPLE else None,
            "amount_max": money(amount_max),
            "target_to_mean_ratio": ratio,
            "target_exceeds_max": target.amount > amount_max,
        }

    counts = Counter(t.category for t in history)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CATEGORIES]
    category_distribution = [
        {
            "category": cat,
            "count": cnt,
            "share": float((Decimal(cnt) / Decimal(n)).quantize(SHARE_Q, rounding=ROUND_HALF_UP)),
        }
        for cat, cnt in ordered
    ]

    same_category = [t for t in history if t.category == target.category]
    target_category = {
        "seen_before": bool(same_category),
        "count": len(same_category),
        "last_seen_at": ts(max(t.trans_at for t in same_category)) if same_category else None,
    }

    recent = sorted(history, key=lambda t: t.transaction_id)
    recent = sorted(recent, key=lambda t: t.trans_at, reverse=True)[:RECENT_LIMIT]

    return {
        "tool": "get_transaction_history",
        "schema_version": schema_version,
        "status": status,
        "evidence_id": f"EV-TH-{target.transaction_id}-{lookback_days}d",
        "basis": {
            "target_transaction_id": target.transaction_id,
            "card_token": target.card_token,
            "as_of": ts(target.trans_at),
            "window_start": ts(window_start(target.trans_at, lookback_days)),
            "lookback_days": lookback_days,
        },
        "target": {
            "trans_at": ts(target.trans_at),
            "amount": money(target.amount),
            "category": target.category,
            "merchant_name": target.merchant_name,
        },
        "stats": stats,
        "category_distribution": category_distribution,
        "target_category": target_category,
        "recent_transactions": [
            {
                "transaction_id": t.transaction_id,
                "trans_at": ts(t.trans_at),
                "amount": money(t.amount),
                "category": t.category,
                "merchant_name": t.merchant_name,
            }
            for t in recent
        ],
        "quality": {"min_sample_required": MIN_SAMPLE, "notes": notes},
    }
