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


def _scope(period: int | None) -> str:
    return f"{period}일 비교 기간" if period else "비교 기간"


def _prefix(period: int | None) -> str:
    return f"{period}일 비교 기간 " if period else ""


# 라벨 템플릿의 {s}는 "비교 기간" 또는 "N일 비교 기간", {p}는 기간이 있을 때만 붙는 접두어입니다.
# 기간은 라벨 자리에만 들어가고, 값({v})은 str.format이 한 번에 치환하므로 값 안의 글자는 바뀌지 않습니다
# (PR #37 리뷰: 완성된 문자열에서 "비교 기간"을 찾아 바꾸면 값이 그 글자를 포함할 때 값이 바뀜).
Renderer = Callable[..., str]


def _bool(true_text: str, false_text: str) -> Renderer:
    def render(v: Any, period: int | None = None) -> str:
        text = true_text if v is True else false_text if v is False else "{p}정보 없음"
        return text.format(s=_scope(period), p=_prefix(period))
    return render


def _fmt(template: str, fn: Callable[[Any], str] = _num) -> Renderer:
    def render(v: Any, period: int | None = None) -> str:
        return template.format(v="정보 없음" if v is None else fn(v), s=_scope(period), p=_prefix(period))
    return render


def _ratio(v: Any, period: int | None = None) -> str:
    if v is None:
        return f"{_prefix(period)}평균 대비 비율 계산 불가"
    return f"{_scope(period)} 평균 대비 {_num(v)}배"


_I = r"[0-9]{1,2}"
CATALOG: list[tuple[re.Pattern[str], Renderer]] = [
    (r"/stats/txn_count", _fmt("{s} 거래 {v}건")),
    (r"/stats/amount_mean", _fmt("{s} 평균 금액 {v}")),
    (r"/stats/amount_median", _fmt("{s} 중앙값 {v}")),
    (r"/stats/amount_p95", _fmt("{s} 95백분위 금액 {v}")),
    (r"/stats/amount_max", _fmt("{s} 최댓값 {v}")),
    (r"/stats/target_to_mean_ratio", _ratio),
    (r"/stats/target_exceeds_max", _bool("{s} 최댓값 초과", "{s} 최댓값 이하")),
    (r"/target/amount", _fmt("대상 금액 {v}")),
    (r"/target/trans_at", _fmt("대상 거래 시각 {v}")),
    (r"/target/category", _fmt("대상 업종 {v}")),
    (r"/target/merchant_name", _fmt("대상 가맹점 {v}")),
    # compute_geo_anomaly (#52). 거리는 가맹점 좌표 사이 거리이며 고객의 실제 위치가 아님을 라벨에 드러냅니다.
    (r"/target/home_distance_km", _fmt("거주지와 대상 가맹점 거리 {v}km")),
    (r"/target/far_from_home", _bool("거주지에서 기준 거리 초과", "거주지에서 기준 거리 이내")),
    (r"/previous/trans_at", _fmt("직전 거래 시각 {v}")),
    (r"/previous/merchant_name", _fmt("직전 거래 가맹점 {v}")),
    (r"/previous/seconds_since", _fmt("직전 거래 후 경과 {v}초")),
    (r"/previous/distance_km", _fmt("직전 거래 가맹점과의 거리 {v}km")),
    (r"/previous/speed_kmh", _fmt("직전 거래 대비 환산 이동 속도 {v}km/h")),
    (r"/previous/exceeds_plausible_speed", _bool("환산 이동 속도 기준 초과", "환산 이동 속도 기준 이내")),
    (r"/basis/speed_threshold_kmh", _fmt("이동 속도 기준 {v}km/h")),
    (r"/basis/home_distance_threshold_km", _fmt("거주지 거리 기준 {v}km")),
    (r"/basis/lookback_days", _fmt("비교 기간 {v}일")),
    (r"/basis/window_start", _fmt("비교 시작 {v}")),
    (r"/basis/as_of", _fmt("기준 시점 {v}")),
    (r"/target_category/seen_before", _bool("{s} 내 이용 이력 있음", "{s} 내 이용 이력 없음")),
    (r"/target_category/count", _fmt("{s} 내 같은 업종 {v}건")),
    (r"/target_category/last_seen_at", _fmt("{p}같은 업종 최근 이용 {v}")),
    (rf"/category_distribution/{_I}/category", _fmt("{p}업종 {v}")),
    (rf"/category_distribution/{_I}/count", _fmt("{p}업종 거래 {v}건")),
    (rf"/category_distribution/{_I}/share", _fmt("{p}업종 비중 {v}", _pct)),
    (rf"/recent_transactions/{_I}/trans_at", _fmt("{p}최근 거래 시각 {v}")),
    (rf"/recent_transactions/{_I}/amount", _fmt("{p}최근 거래 금액 {v}")),
    (rf"/recent_transactions/{_I}/category", _fmt("{p}최근 거래 업종 {v}")),
    (rf"/recent_transactions/{_I}/merchant_name", _fmt("{p}최근 거래 가맹점 {v}")),
]
CATALOG = [(re.compile(f"^{p}$"), fn) for p, fn in CATALOG]  # type: ignore[misc]


def renderer_for(path: str) -> Renderer | None:
    for pattern, fn in CATALOG:
        if pattern.match(path):
            return fn
    return None


# 조회 기간에 따라 값이 달라지는 경로 (#36). /target/*(대상 거래 자체)와 /basis/*(기준 정보)는 제외합니다.
PERIOD_SCOPED = ("/stats/", "/target_category/", "/category_distribution/", "/recent_transactions/")
# 근거 ID 형식 EV-<툴>-<대상>-<기간>d. 검증 V10과 기간 라벨이 같은 정의를 씁니다 (Gemini 리뷰 #37).
EVIDENCE_ID = re.compile(r"^(EV-[A-Z]{2}-[A-Za-z0-9]{1,64})-([0-9]{1,3})d$")


def render_evidence(ref: dict[str, Any]) -> str:
    """근거 하나를 구절로 바꿉니다. 기간 의존 경로는 근거 ID의 조회 기간을 라벨에 붙입니다.

    같은 보고서에 90일·365일 근거가 함께 있을 때 [비교 기간 평균 대비 …]만으로는 어느 기간의
    값인지 구분할 수 없기 때문입니다(27B high 관찰). 기간은 모델 문장이 아니라 근거 ID에서 읽고,
    라벨 템플릿의 기간 자리에만 들어갑니다.
    """
    fn = renderer_for(ref["path"])
    assert fn is not None
    m = EVIDENCE_ID.match(ref.get("evidence_id", ""))
    period = int(m.group(2)) if m and ref["path"].startswith(PERIOD_SCOPED) else None
    return fn(ref["value"], period)


def render_statement(statement: str, evidence: list[dict[str, Any]]) -> str:
    """검증(V3·V5)을 통과한 문장의 자리표시자를 [라벨 값]으로 채웁니다."""
    return PLACEHOLDER.sub(lambda m: f"[{render_evidence(evidence[int(m.group(1))])}]", statement)
