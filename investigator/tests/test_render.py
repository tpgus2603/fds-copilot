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
