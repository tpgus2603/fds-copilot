"""렌더링 카탈로그 (docs/04 4.2): 값은 다시 반올림하지 않고, 라벨·단위를 붙여 표시합니다."""

import pytest

from fds_investigator.agent.render import render_statement, renderer_for


@pytest.mark.parametrize("path,value,expected", [
    ("/stats/txn_count", 5, "비교 기간 거래 5건"),
    ("/stats/amount_mean", "30.00", "비교 기간 평균 금액 30.00"),
    ("/stats/amount_p95", None, "비교 기간 95백분위 금액 정보 없음"),
    ("/stats/target_to_mean_ratio", 20.23, "비교 기간 평균 대비 20.23배"),
    ("/stats/target_to_mean_ratio", None, "평균 대비 비율 계산 불가"),
    ("/stats/target_exceeds_max", False, "비교 기간 최댓값 이하"),
    ("/target_category/seen_before", True, "비교 기간 내 이용 이력 있음"),
    ("/category_distribution/0/share", 0.4286, "업종 비중 42.86%"),
    ("/category_distribution/1/share", 0.0, "업종 비중 0%"),
    ("/recent_transactions/9/amount", "35.10", "최근 거래 금액 35.10"),
    ("/target/trans_at", "2020-06-21T12:00:00", "대상 거래 시각 2020-06-21T12:00:00"),
])
def test_catalog(path, value, expected):
    assert renderer_for(path)(value) == expected


@pytest.mark.parametrize("path", ["/quality/notes/0", "/evidence_id", "/stats", "/recent_transactions/100/amount"])
def test_paths_outside_catalog(path):
    assert renderer_for(path) is None


def test_render_statement_uses_evidence_order():
    ev = [{"path": "/stats/txn_count", "value": 5}, {"path": "/stats/target_exceeds_max", "value": True}]
    assert render_statement("{1}, {0}", ev) == "[비교 기간 최댓값 초과], [비교 기간 거래 5건]"


# ---------- 조회 기간 표시 (#36) ----------

def _e(eid, path, value):
    return {"evidence_id": eid, "path": path, "value": value}


@pytest.mark.parametrize("ref,expected", [
    (_e("EV-TH-high-90d", "/stats/target_to_mean_ratio", 10.0), "90일 비교 기간 평균 대비 10.0배"),
    (_e("EV-TH-high-365d", "/stats/target_to_mean_ratio", 1.71), "365일 비교 기간 평균 대비 1.71배"),
    (_e("EV-TH-high-365d", "/stats/target_exceeds_max", False), "365일 비교 기간 최댓값 이하"),
    (_e("EV-TH-high-90d", "/target_category/seen_before", False), "90일 비교 기간 내 이용 이력 없음"),
    (_e("EV-TH-high-90d", "/stats/target_to_mean_ratio", None), "90일 비교 기간 평균 대비 비율 계산 불가"),
    # "비교 기간"이 없는 기간 의존 구절에는 앞에 붙임
    (_e("EV-TH-high-90d", "/category_distribution/0/category", "grocery_pos"), "90일 비교 기간 업종 grocery_pos"),
    (_e("EV-TH-high-365d", "/recent_transactions/0/amount", "900.00"), "365일 비교 기간 최근 거래 금액 900.00"),
    # 기간과 무관한 경로는 그대로
    (_e("EV-TH-high-365d", "/target/category", "shopping_net"), "대상 업종 shopping_net"),
    (_e("EV-TH-high-365d", "/basis/lookback_days", 365), "비교 기간 365일"),
    # 근거 ID가 없으면(단위 테스트용 근거) 기존 라벨
    ({"path": "/stats/txn_count", "value": 5}, "비교 기간 거래 5건"),
])
def test_period_is_shown_from_evidence_id(ref, expected):
    from fds_investigator.agent.render import render_evidence
    assert render_evidence(ref) == expected


def test_27b_high_case_periods_are_distinguishable():
    stmt = "대상 금액이 {0}입니다."
    short = render_statement(stmt, [_e("EV-TH-high-90d", "/stats/target_to_mean_ratio", 10.0)])
    long = render_statement(stmt, [_e("EV-TH-high-365d", "/stats/target_to_mean_ratio", 1.71)])
    assert "90일" in short and "365일" in long
