"""가맹점 툴의 조사 루프 통합 (#56): 렌더링, 극성, 표본 부족 규칙, 중복 판정, 프롬프트. 실제 툴 서버 사용."""

from pathlib import Path

import pytest
from agent_fakes import FakeModel, high_report, respond, submit

from fds_investigator.agent.loop import Limits, _canonical, investigate
from fds_investigator.agent.prompts import SYSTEM_PROMPT, citable_paths
from fds_investigator.agent.render import render_evidence
from fds_investigator.agent.tool_session import McpToolSession
from fds_investigator.agent.verify import verify_report
from fds_investigator.load_csv import load

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
TYP, RARE = "EV-MP-merchtypical-90d", "EV-MP-merchrare-90d"


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("merchagent") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


def call(target: str) -> dict:
    return {"type": "tool_use", "id": "call_m", "name": "get_merchant_profile", "input": {"target_transaction_id": target}}


def ev(eid, path, value):
    return {"evidence_id": eid, "path": path, "value": value}


def report(*findings, rec="further_review_recommended"):
    return {"summary": "조사 결과를 요약합니다.", "recommendation": rec, "findings": list(findings)}


async def snaps(db, target) -> dict:
    r = await investigate(target, FakeModel(respond(call(target)), respond(submit(high_report()))),
                          lambda t: McpToolSession(t, db), "fake", Limits(max_report_repairs=0))
    return r.tool_results


async def test_mixed_evidence_report_is_reported_and_rendered(db):
    s = await snaps(db, "merchtypical")
    ratio = s[TYP]["merchant_stats"]["target_to_mean_ratio"]
    rep = report({"kind": "suspicion", "statement": "카드 이력 기준으로 {0}입니다.",
                  "evidence": [ev("EV-TH-merchtypical-90d", "/stats/target_exceeds_max", True)]},
                 {"kind": "counter", "statement": "가맹점 기준으로는 {0}이며 {1}입니다.",
                  "evidence": [ev(TYP, "/merchant_stats/target_to_mean_ratio", ratio),
                               ev(TYP, "/merchant_stats/target_exceeds_max", False)]})
    r = await investigate("merchtypical", FakeModel(respond(call("merchtypical")), respond(submit(rep))),
                          lambda t: McpToolSession(t, db), "fake", Limits(max_report_repairs=0))
    assert r.outcome.status == "reported", r.outcome
    assert r.rendered["findings"][1]["text"] == (
        f"가맹점 기준으로는 [90일 비교 기간 가맹점 평균 대비 {ratio}배]이며 [90일 비교 기간 가맹점 최댓값 이하]입니다.")


async def test_merchant_polarity_and_sparse_rules(db):
    s = await snaps(db, "merchtypical")
    wrong = report({"kind": "suspicion", "statement": "가맹점 기준 {0}입니다.",
                    "evidence": [ev(TYP, "/merchant_stats/target_exceeds_max", False)]})
    assert verify_report(wrong, s).code == "POLARITY_CONFLICT"
    first_visit_as_counter = report({"kind": "counter", "statement": "이 가맹점은 {0}입니다.",
                                     "evidence": [ev(TYP, "/card_at_merchant/seen_before", False)]}, rec="no_anomaly_found")
    assert verify_report(first_visit_as_counter, s).code == "POLARITY_CONFLICT"
    rare = await snaps(db, "merchrare")
    sparse = report({"kind": "counter", "statement": "가맹점 거래는 {0}입니다.",
                     "evidence": [ev(RARE, "/merchant_stats/txn_count", 5)]}, rec="no_anomaly_found")
    assert verify_report(sparse, rare).code == "SPARSE_HISTORY_DIRECTION"     # 다른 카드 2장: 방향 판정 불가
    as_gap = report({"kind": "gap", "statement": "가맹점 표본은 {0}이라 비교가 제한됩니다.",
                     "evidence": [ev(RARE, "/merchant_stats/card_count", 2)]}, rec="insufficient_evidence")
    assert verify_report(as_gap, rare).ok


async def test_suppressed_amount_cannot_be_cited_as_value(db):
    # insufficient 결과의 amount_mean은 null: null 인용은 통과하지만 수치를 지어내면 값 불일치
    rare = await snaps(db, "merchrare")
    made_up = report({"kind": "gap", "statement": "가맹점 평균은 {0}입니다.",
                      "evidence": [ev(RARE, "/merchant_stats/amount_mean", "50.00")]}, rec="insufficient_evidence")
    assert verify_report(made_up, rare).code == "EVIDENCE_VALUE_MISMATCH"


def test_labels_say_other_cards_and_never_fraud_rate():
    for path, value in [("/merchant_stats/txn_count", 12), ("/merchant_stats/card_count", 6)]:
        assert "다른 카드" in render_evidence({"evidence_id": TYP, "path": path, "value": value})
    texts = [render_evidence({"evidence_id": TYP, "path": p, "value": v}) for p, v in [
        ("/merchant_stats/target_to_mean_ratio", None), ("/merchant_stats/target_exceeds_max", True),
        ("/card_at_merchant/seen_before", False)]]
    assert texts == ["90일 비교 기간 가맹점 평균 대비 비율 없음", "90일 비교 기간 가맹점 최댓값 초과",
                     "90일 비교 기간 이 카드의 이 가맹점 이용 이력 없음"]
    assert not any("사기" in t for t in texts)


def test_dedupe_prompt_and_catalog():
    assert _canonical("get_merchant_profile", {"target_transaction_id": "x"}) == \
        _canonical("get_merchant_profile", {"target_transaction_id": "x", "lookback_days": 90.0})
    listed = ", ".join(citable_paths())
    assert "/merchant_stats/target_exceeds_max" in listed and listed in SYSTEM_PROMPT
    assert "get_merchant_profile로 확인합니다" in SYSTEM_PROMPT and "사기율이 아닙니다" in SYSTEM_PROMPT
