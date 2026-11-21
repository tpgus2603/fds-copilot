"""보고서 검증 V8~V10 (#30): 로컬 9B 실제 실행에서 V1~V7을 통과한 해석 오류를 고정합니다.

스냅샷은 실제 MCP 툴 서버가 합성 데이터로 돌려준 결과입니다.
  high:  대상 업종 shopping_net, 최다 업종 grocery_pos, 90일 최댓값 초과 true / 365일 false (365일 창에 900.00 거래)
  usual: 대상 업종 grocery_pos, 같은 업종 이용 이력 있음, 최댓값 초과 false
"""

import asyncio
import copy
from pathlib import Path

import pytest
from agent_fakes import FakeModel, high_report, history, respond, submit

from fds_investigator.agent.loop import Limits, investigate
from fds_investigator.agent.tool_session import McpToolSession
from fds_investigator.agent.verify import verify_report
from fds_investigator.load_csv import load

ROOT = Path(__file__).resolve().parents[2]
NEW_CODES = {"EVIDENCE_NOT_REFERENCED", "MIXED_LOOKBACK", "POLARITY_CONFLICT", "SPARSE_HISTORY_DIRECTION"}


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("v8") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


@pytest.fixture(scope="module")
def snaps(db) -> dict[str, dict]:
    async def collect() -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in ("high", "usual"):
            model = FakeModel(respond(history(t, 365)), respond(submit(high_report())))
            r = await investigate(t, model, lambda x: McpToolSession(x, db), "fake", Limits())
            out |= r.tool_results
        return out
    return asyncio.run(collect())


def ev(eid: str, path: str, value) -> dict:
    return {"evidence_id": eid, "path": path, "value": value}


def report(*findings: dict, rec: str = "further_review_recommended", summary: str = "조사 결과를 요약합니다.") -> dict:
    return {"summary": summary, "findings": list(findings), "recommendation": rec}


def f(kind: str, statement: str, *evidence: dict) -> dict:
    return {"kind": kind, "statement": statement, "evidence": list(evidence)}


H90, H365, U90 = "EV-TH-high-90d", "EV-TH-high-365d", "EV-TH-usual-90d"
EXCEEDS, SEEN, TOP = "/stats/target_exceeds_max", "/target_category/seen_before", "/category_distribution/0/category"

# ---------- 거부되어야 하는 보고서 (9B 관찰 사례 포함) ----------

REJECT = {
    # 9B high·usual: 자리표시자 없이 근거만 붙임
    "no-placeholder-9b": (report(
        f("suspicion", "대상 금액이 비교 기간 최댓값을 넘습니다.", ev(H90, EXCEEDS, True))), "EVIDENCE_NOT_REFERENCED"),
    "partial-placeholder": (report(
        f("suspicion", "대상 금액이 {0}입니다.", ev(H90, EXCEEDS, True), ev(H90, SEEN, False))), "EVIDENCE_NOT_REFERENCED"),
    "gap-without-placeholder": (report(
        f("suspicion", "대상은 {0}입니다.", ev(H90, EXCEEDS, True)),
        f("gap", "비교 표본이 적습니다.", ev(H90, "/stats/txn_count", 5))), "EVIDENCE_NOT_REFERENCED"),
    # 9B high: 90일(true)과 365일 근거를 한 발견 사항에 섞음
    "mixed-90-365-9b": (report(
        f("suspicion", "대상은 {0}이고 장기로는 {1}입니다.", ev(H90, EXCEEDS, True), ev(H365, "/stats/txn_count", 6))),
        "MIXED_LOOKBACK"),
    # 9B high: 365일의 false를 의심 근거로 씀
    "exceeds-false-as-suspicion": (report(
        f("suspicion", "대상은 {0}입니다.", ev(H365, EXCEEDS, False))), "POLARITY_CONFLICT"),
    "exceeds-true-as-counter": (report(
        f("counter", "대상은 {0}입니다.", ev(H90, EXCEEDS, True)), rec="no_anomaly_found"), "POLARITY_CONFLICT"),
    "seen-true-as-suspicion": (report(
        f("suspicion", "대상 업종은 {0}입니다.", ev(U90, SEEN, True))), "POLARITY_CONFLICT"),
    "seen-false-as-counter": (report(
        f("counter", "대상 업종은 {0}입니다.", ev(H90, SEEN, False)), rec="no_anomaly_found"), "POLARITY_CONFLICT"),
    # 9B high: 최다 업종 grocery_pos를 대상 업종(shopping_net)과 일치한다는 정상 정황으로 씀
    "other-category-as-counter-9b": (report(
        f("suspicion", "대상은 {0}입니다.", ev(H90, EXCEEDS, True)),
        f("counter", "주로 이용하던 {0} 업종과 같습니다.", ev(H90, TOP, "grocery_pos"))), "POLARITY_CONFLICT"),
}


@pytest.mark.parametrize("rep,code", REJECT.values(), ids=REJECT.keys())
def test_new_rules_reject(snaps, rep, code):
    r = verify_report(rep, snaps)
    assert not r.ok and code in {x["code"] for x in r.failures}, r.failures
    # V1~V7만으로는 통과하던 보고서임: 실패는 모두 새 규칙에서만 나옴
    assert {x["code"] for x in r.failures} <= NEW_CODES, r.failures


# ---------- 통과해야 하는 보고서 ----------

ACCEPT = {
    "fixture-high-report": high_report(),
    # 기간별로 발견 사항을 나누면 90일과 365일을 함께 쓸 수 있음
    "periods-split": report(
        f("suspicion", "최근 기간에는 {0}입니다.", ev(H90, EXCEEDS, True)),
        f("gap", "장기 기간에는 {0}이어서 판단이 갈립니다.", ev(H365, EXCEEDS, False))),
    # gap에는 방향 규칙을 적용하지 않음
    "gap-exempt-from-polarity": report(
        f("suspicion", "대상 업종은 {0}입니다.", ev(H90, SEEN, False)),
        f("gap", "장기로는 {0}입니다.", ev(H365, EXCEEDS, False))),
    # 대상 업종과 같은 업종을 counter로 인용하는 것은 정상
    "same-category-as-counter": report(
        f("counter", "주 이용 업종 {0}이며 {1}입니다.", ev(U90, TOP, "grocery_pos"), ev(U90, SEEN, True)),
        f("counter", "대상은 {0}입니다.", ev(U90, EXCEEDS, False)), rec="no_anomaly_found"),
    # 다른 업종은 suspicion의 근거로는 쓸 수 있음 (주 업종과 다른 업종에서 결제)
    "other-category-as-suspicion": report(
        f("suspicion", "주 이용 업종은 {0}이지만 대상 업종은 {1}입니다.",
          ev(H90, TOP, "grocery_pos"), ev(H90, "/target/category", "shopping_net"))),
    # 같은 자리표시자를 두 번 써도 됨
    "placeholder-repeated": report(f("suspicion", "{0}. 다시 말해 {0}입니다.", ev(H90, EXCEEDS, True))),
}


@pytest.mark.parametrize("rep", ACCEPT.values(), ids=ACCEPT.keys())
def test_valid_reports_still_pass(snaps, rep):
    r = verify_report(copy.deepcopy(rep), snaps)
    assert r.ok, r.failures


def test_first_failure_code_keeps_v1_v7_priority(snaps):
    # 기존 규칙 위반이 함께 있으면 기존 사유 코드가 대표 코드
    rep = report(f("suspicion", "근거 없음", ev(H90, EXCEEDS, True)), f("suspicion", "{5}", ev(H90, EXCEEDS, True)))
    assert verify_report(rep, snaps).code == "PLACEHOLDER_INVALID"


@pytest.mark.anyio
async def test_loop_marks_9b_style_report_incomplete(db):
    bad = REJECT["no-placeholder-9b"][0]
    r = await investigate("high", FakeModel(respond(submit(bad))), lambda x: McpToolSession(x, db), "fake",
                          Limits(max_report_repairs=0))
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", "EVIDENCE_NOT_REFERENCED")



# ---------- 빈·부족 이력의 방향 판정 금지 (PR #31 리뷰) ----------

@pytest.fixture(scope="module")
def sparse_snaps(db) -> dict[str, dict]:
    async def collect() -> dict[str, dict]:
        r = await investigate("new", FakeModel(respond(submit(high_report()))), lambda x: McpToolSession(x, db),
                              "fake", Limits())
        return r.tool_results
    snaps = asyncio.run(collect())
    assert snaps["EV-TH-new-90d"]["status"] == "empty"
    return snaps


NEW90 = "EV-TH-new-90d"


def test_empty_history_cannot_be_sole_suspicion(sparse_snaps):
    # Codex 재현: new의 seen_before=false 하나로 further_review_recommended → 이전에는 reported
    rep = report(f("suspicion", "대상 업종은 {0}입니다.", ev(NEW90, SEEN, False)))
    r = verify_report(rep, sparse_snaps)
    assert not r.ok and r.code == "SPARSE_HISTORY_DIRECTION"


def test_empty_history_cannot_be_counter(sparse_snaps):
    rep = report(f("counter", "비교 거래는 {0}입니다.", ev(NEW90, "/stats/txn_count", 0)), rec="no_anomaly_found")
    assert verify_report(rep, sparse_snaps).code == "SPARSE_HISTORY_DIRECTION"


def test_empty_history_is_fine_as_gap_and_target_fields_still_directional(sparse_snaps):
    ok = report(f("gap", "비교 이력이 {0}이고 {1}입니다.", ev(NEW90, "/stats/txn_count", 0), ev(NEW90, SEEN, False)),
                rec="insufficient_evidence")
    assert verify_report(ok, sparse_snaps).ok
    # 대상 거래 자체의 필드는 이력과 무관하므로 제한하지 않음 (다른 규칙만 적용)
    target_only = report(f("gap", "대상 업종은 {0}입니다.", ev(NEW90, "/target/category", "grocery_pos")),
                         rec="insufficient_evidence")
    assert verify_report(target_only, sparse_snaps).ok


def test_insufficient_status_is_treated_like_empty():
    from fds_investigator.agent.verify import _sparse_history_direction
    snap = {"status": "insufficient"}
    assert _sparse_history_direction("suspicion", "/target_category/seen_before", snap)
    assert _sparse_history_direction("gap", "/target_category/seen_before", snap) is None
    assert _sparse_history_direction("suspicion", "/target/amount", snap) is None
    assert _sparse_history_direction("suspicion", "/stats/txn_count", {"status": "ok"}) is None


# ---------- 패턴 위반이 있어도 실패를 모두 모음 (#38, 27B r1 관찰) ----------

N90, N365 = "EV-TH-new-90d", "EV-TH-new-365d"


@pytest.fixture(scope="module")
def new_snaps(db) -> dict[str, dict]:
    async def collect() -> dict[str, dict]:
        model = FakeModel(respond(history("new", 365)), respond(submit(high_report())))
        r = await investigate("new", model, lambda x: McpToolSession(x, db), "fake", Limits(max_report_repairs=0))
        return r.tool_results
    return asyncio.run(collect())


def test_summary_pattern_error_does_not_hide_other_failures(new_snaps):
    # 27B new r1 첫 제출: 요약에 숫자, 문장에 원문자(①), 근거 하나 미참조가 함께 있었지만
    # 요약 오류만 보고되어 모델이 요약만 고친 뒤 다시 실패했습니다.
    rep = report(
        f("gap", "기본 ①일 비교 기간에 과거 거래가 {0}건으로 없습니다.",
          ev(N90, "/basis/lookback_days", 90), ev(N90, "/stats/txn_count", 0)),
        rec="insufficient_evidence", summary="90일과 365일 모두 과거 거래가 없습니다.")
    r = verify_report(rep, new_snaps)
    got = {(x["code"], x["where"]) for x in r.failures}
    assert ("REPORT_SCHEMA_INVALID", "/summary") in got
    assert ("REPORT_SCHEMA_INVALID", "/findings/0/statement") in got        # ① (유니코드 No 범주)
    assert ("EVIDENCE_NOT_REFERENCED", "/findings/0/evidence/1") in got     # 이전에는 가려졌던 실패
    assert r.code == "REPORT_SCHEMA_INVALID"


def test_placeholders_in_summary_are_reported_with_the_rest(snaps):
    # 27B usual r1 첫 제출: 요약에 {0}~{4}
    rep = report(f("counter", "대상 업종 {0}입니다.", ev(U90, SEEN, True), ev(U90, "/target/category", "grocery_pos")),
                 rec="no_anomaly_found", summary="최근 {0}의 평균과 일치합니다.")
    got = {(x["code"], x["where"]) for x in verify_report(rep, snaps).failures}
    assert got == {("REPORT_SCHEMA_INVALID", "/summary"), ("EVIDENCE_NOT_REFERENCED", "/findings/0/evidence/1")}


@pytest.mark.parametrize("rep", [
    {"summary": "요약", "findings": "목록 아님", "recommendation": "further_review_recommended"},
    report(f("suspicion", "{0}", {"evidence_id": H90, "path": EXCEEDS})),               # value 누락
    {"summary": "요약", "findings": [], "recommendation": "unknown"},
], ids=["findings-not-list", "evidence-missing-value", "bad-enum"])
def test_structural_errors_still_stop_early(snaps, rep):
    r = verify_report(rep, snaps)
    assert not r.ok and {x["code"] for x in r.failures} == {"REPORT_SCHEMA_INVALID"}



def test_structural_error_is_listed_first_even_with_many_pattern_errors(snaps):
    # Codex 재현: 패턴 위반 7개 뒤의 evidence 누락이 errors[:5]에서 잘려 모델에 전달되지 않음
    findings = [f("suspicion", f"문장 {i}입니다 {{0}}", ev(H90, EXCEEDS, True)) for i in range(6)]
    findings.append({"kind": "gap", "statement": "근거 필드 누락"})
    rep = report(*findings, summary="요약 1")
    r = verify_report(rep, snaps)
    first = r.failures[0]
    assert first["where"] == "/findings/6" and "evidence" in first["detail"]
    assert len(r.failures) <= 11
    total_pattern = 7
    shown = sum(1 for x in r.failures if x["where"].endswith("/statement") or x["where"] == "/summary")
    omitted = [x for x in r.failures if x["where"] == ""]
    assert shown + (int(omitted[0]["detail"].split()[3][:-1]) if omitted else 0) == total_pattern


# ---------- #40, Gemini 리뷰 후속 ----------

def test_same_location_violation_is_reported_once(snaps):
    rep = report(f("suspicion", "대상은 10배이고 {0}입니다.", ev(H90, EXCEEDS, True)))
    stmt = [x for x in verify_report(rep, snaps).failures if x["where"] == "/findings/0/statement"]
    assert len(stmt) == 1 and stmt[0]["rule"] == "pattern"


def test_polarity_is_not_judged_on_a_miscited_value(snaps):
    # 실제 false를 true로 잘못 인용: 값 불일치만 보고하고, 잘못된 값으로 방향을 다시 판정하지 않음
    rep = report(f("counter", "대상은 {0}입니다.", ev(H365, EXCEEDS, True)), rec="no_anomaly_found")
    codes = [x["code"] for x in verify_report(rep, snaps).failures]
    assert codes == ["EVIDENCE_VALUE_MISMATCH"]


@pytest.mark.parametrize("summary,rule", [("", "minLength"), (5, "type"), ("요약 1", "pattern")])
def test_summary_schema_failures_carry_rule(snaps, summary, rule):
    rep = report(f("gap", "표본은 {0}입니다.", ev(H90, "/stats/txn_count", 5)), rec="insufficient_evidence")
    rep["summary"] = summary
    got = [x for x in verify_report(rep, snaps).failures if x["where"] == "/summary"]
    assert got and got[0]["rule"] == rule


def test_evidence_id_format_is_defined_once():
    from fds_investigator.agent import render, verify
    assert verify._EVIDENCE_ID is render.EVIDENCE_ID
