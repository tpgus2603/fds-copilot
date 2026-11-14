"""렌더링 카탈로그 (docs/04 4.2).

모델은 보고서 문장에 숫자를 쓰지 않고 {0} 같은 자리표시자로 근거를 가리킵니다. 서버가 근거 경로의
라벨·단위로 값을 채워, 숫자의 의미를 문장 표현이 아니라 근거 필드에 묶습니다.
카탈로그에 없는 경로는 인용할 수 없습니다(V3).
"""

import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PLACEHOLDER = re.compile(r"\{([0-9])\}")


def _num(v: Any) -> str:
    """툴이 준 값을 그대로 표시합니다(다시 반올림하지 않음). 금액은 이미 소수 둘째 자리 문자열입니다."""
    return str(v)


def _pct(v: Any) -> str:
    return f"{(Decimal(repr(v)) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize():f}%"


def _bool(true_text: str, false_text: str) -> Callable[[Any], str]:
    return lambda v: true_text if v is True else false_text if v is False else "정보 없음"


def _fmt(template: str, fn: Callable[[Any], str] = _num) -> Callable[[Any], str]:
    return lambda v: template.format(v="정보 없음" if v is None else fn(v))


_I = r"[0-9]{1,2}"
CATALOG: list[tuple[re.Pattern[str], Callable[[Any], str]]] = [
    (r"/stats/txn_count", _fmt("비교 기간 거래 {v}건")),
    (r"/stats/amount_mean", _fmt("비교 기간 평균 금액 {v}")),
    (r"/stats/amount_median", _fmt("비교 기간 중앙값 {v}")),
    (r"/stats/amount_p95", _fmt("비교 기간 95백분위 금액 {v}")),
    (r"/stats/amount_max", _fmt("비교 기간 최댓값 {v}")),
    (r"/stats/target_to_mean_ratio", lambda v: f"비교 기간 평균 대비 {_num(v)}배" if v is not None else "평균 대비 비율 계산 불가"),
    (r"/stats/target_exceeds_max", _bool("비교 기간 최댓값 초과", "비교 기간 최댓값 이하")),
    (r"/target/amount", _fmt("대상 금액 {v}")),
    (r"/target/trans_at", _fmt("대상 거래 시각 {v}")),
    (r"/target/category", _fmt("대상 업종 {v}")),
    (r"/target/merchant_name", _fmt("대상 가맹점 {v}")),
    (r"/basis/lookback_days", _fmt("비교 기간 {v}일")),
    (r"/basis/window_start", _fmt("비교 시작 {v}")),
    (r"/basis/as_of", _fmt("기준 시점 {v}")),
    (r"/target_category/seen_before", _bool("비교 기간 내 이용 이력 있음", "비교 기간 내 이용 이력 없음")),
    (r"/target_category/count", _fmt("비교 기간 내 같은 업종 {v}건")),
    (r"/target_category/last_seen_at", _fmt("같은 업종 최근 이용 {v}")),
    (rf"/category_distribution/{_I}/category", _fmt("업종 {v}")),
    (rf"/category_distribution/{_I}/count", _fmt("업종 거래 {v}건")),
    (rf"/category_distribution/{_I}/share", _fmt("업종 비중 {v}", _pct)),
    (rf"/recent_transactions/{_I}/trans_at", _fmt("최근 거래 시각 {v}")),
    (rf"/recent_transactions/{_I}/amount", _fmt("최근 거래 금액 {v}")),
    (rf"/recent_transactions/{_I}/category", _fmt("최근 거래 업종 {v}")),
    (rf"/recent_transactions/{_I}/merchant_name", _fmt("최근 거래 가맹점 {v}")),
]
CATALOG = [(re.compile(f"^{p}$"), fn) for p, fn in CATALOG]  # type: ignore[misc]


def renderer_for(path: str) -> Callable[[Any], str] | None:
    for pattern, fn in CATALOG:
        if pattern.match(path):
            return fn
    return None


def render_statement(statement: str, evidence: list[dict[str, Any]]) -> str:
    """검증(V3·V5)을 통과한 문장의 자리표시자를 [라벨 값]으로 채웁니다."""
    def repl(m: re.Match[str]) -> str:
        ref = evidence[int(m.group(1))]
        fn = renderer_for(ref["path"])
        assert fn is not None
        return f"[{fn(ref['value'])}]"
    return PLACEHOLDER.sub(repl, statement)
