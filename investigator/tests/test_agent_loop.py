"""조사 루프 (docs/04): 가짜 모델 + 실제 MCP 툴 서버(합성 데이터)."""

import json
from pathlib import Path

import pytest
from agent_fakes import (
    FakeModel,
    api_error,
    high_report,
    history,
    respond,
    submit,
    text,
)

from fds_investigator.agent.loop import Limits, investigate
from fds_investigator.agent.tool_session import McpToolSession
from fds_investigator.load_csv import load

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
MODEL = "fake-model"


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("agent") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


async def run(db: Path, model: FakeModel, target: str = "high", **limits):
    return await investigate(target, model, lambda t: McpToolSession(t, db), MODEL, Limits(**limits))


def step_types(result) -> list[str]:
    return [s["type"] for s in result.steps]


# ---------- 정상 경로 ----------

async def test_baseline_then_submit_reports(db):
    model = FakeModel(respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported", r.outcome
    assert step_types(r)[:2] == ["tool_server_started", "baseline_tool_call"]
    assert set(r.tool_results) == {"EV-TH-high-90d"}
    assert r.rendered["findings"][0]["text"] == "대상 금액이 [90일 비교 기간 평균 대비 10.0배]이며 [90일 비교 기간 최댓값 초과]입니다."
    assert r.rendered["findings"][2]["text"] == "비교 표본이 [90일 비교 기간 거래 5건]으로 최소 기준만 충족합니다."
    assert r.rendered["findings"][0]["evidence"][0] == f"{r.run_id}/EV-TH-high-90d#/stats/target_to_mean_ratio"
    assert r.usage.model_calls == 1 and r.usage.tool_calls == 0
    # 첫 요청에 시스템 프롬프트, 조회 툴 + submit_report, 기본 증거가 들어감
    req = model.requests[0]
    assert [t["name"] for t in req.tools] == ["get_transaction_history", "compute_geo_anomaly", "submit_report"]
    assert "EV-TH-high-90d" in req.messages[0]["content"]


async def test_additional_lookup_then_cite_it(db):
    # 7일 결과는 표본 부족(insufficient)이므로 gap으로 인용 (PR #31 리뷰: 부족한 이력은 방향을 정하지 못함)
    report = high_report(findings=[{"kind": "gap", "statement": "짧은 기간 표본은 {0}입니다.",
                                    "evidence": [{"evidence_id": "EV-TH-high-7d", "path": "/stats/txn_count", "value": 4}]}],
                         recommendation="insufficient_evidence")
    model = FakeModel(respond(history("high", 7)), respond(submit(report)))
    r = await run(db, model)
    assert r.outcome.status == "reported", r.outcome
    assert set(r.tool_results) == {"EV-TH-high-90d", "EV-TH-high-7d"}
    assert r.usage.tool_calls == 1
    # 두 번째 요청에 tool_result가 tool_use 순서대로 들어감
    tr = model.requests[1].messages[-1]["content"][0]
    assert tr["type"] == "tool_result" and json.loads(tr["content"])["evidence_id"] == "EV-TH-high-7d"


async def test_empty_baseline_proceeds(db):
    report = {"summary": "비교 기간 이력이 없어 평소 패턴과 비교할 수 없습니다.",
              "findings": [{"kind": "gap", "statement": "{0}입니다.",
                            "evidence": [{"evidence_id": "EV-TH-new-90d", "path": "/stats/txn_count", "value": 0}]}],
              "recommendation": "insufficient_evidence"}
    r = await run(db, FakeModel(respond(submit(report))), target="new")
    assert r.outcome.status == "reported", r.outcome
    assert r.tool_results["EV-TH-new-90d"]["status"] == "empty"


# ---------- 제출 규칙 (2.6) ----------

async def test_mixed_submit_rejected_then_sole_submit(db):
    model = FakeModel(respond(history("high", 30), submit(high_report())), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    assert "submit_rejected" in step_types(r)
    blocks = model.requests[1].messages[-1]["content"]
    assert [json.loads(b["content"]).get("error", {}).get("code") for b in blocks] == [None, "MIXED_WITH_TOOLS"]
    assert r.usage.tool_calls == 1 and r.usage.rejected_calls == 1


async def test_duplicate_submit_rejected(db):
    model = FakeModel(respond(submit(high_report()), submit(high_report())), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    codes = [json.loads(b["content"])["error"]["code"] for b in model.requests[1].messages[-1]["content"]]
    assert codes == ["DUPLICATE_SUBMIT", "DUPLICATE_SUBMIT"]


# ---------- 툴 호출 규칙 (2.4, 2.5) ----------

async def test_unknown_and_duplicate_lookups_not_executed(db):
    model = FakeModel(respond(history("high", 90), {"type": "tool_use", "id": "toolu_x", "name": "delete_case", "input": {}}),
                      respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    codes = [json.loads(b["content"])["error"]["code"] for b in model.requests[1].messages[-1]["content"]]
    assert codes == ["DUPLICATE_CALL", "UNKNOWN_TOOL"]
    assert r.usage.tool_calls == 0 and r.usage.rejected_calls == 2


async def test_batch_over_remaining_limit_rejected_whole(db):
    model = FakeModel(respond(history("high", 30), history("high", 60)), respond(submit(high_report())))
    r = await run(db, model, max_tool_calls=1)
    assert r.outcome.status == "reported"
    codes = [json.loads(b["content"])["error"]["code"] for b in model.requests[1].messages[-1]["content"]]
    assert codes == ["LIMIT_EXCEEDED", "LIMIT_EXCEEDED"]
    assert r.usage.tool_calls == 0 and set(r.tool_results) == {"EV-TH-high-90d"}


async def test_other_target_blocked_by_tool_server(db):
    """프롬프트 인젝션 등으로 다른 거래를 조회하려 해도 툴 서버가 범위를 강제합니다."""
    model = FakeModel(respond(history("usual", 30)), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    blk = model.requests[1].messages[-1]["content"][0]
    assert blk["is_error"] is True and json.loads(blk["content"])["error"]["code"] == "OUT_OF_SCOPE"


# ---------- 미완료 (5장) ----------

async def test_baseline_unavailable(db):
    r = await run(db, FakeModel(), target="missing")    # 툴 서버 허용 목록에는 있지만 존재하지 않음
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", "BASELINE_UNAVAILABLE")
    assert r.usage.model_calls == 0


async def test_max_model_calls(db):
    model = FakeModel(*[respond(history("high", d)) for d in (10, 20, 30)])
    r = await run(db, model, max_model_calls=3)
    assert r.outcome.reason == "MAX_MODEL_CALLS"
    assert r.usage.model_calls == 3 and len(r.tool_results) == 4


@pytest.mark.parametrize("first,reason", [
    (lambda: text("끝", stop="max_tokens"), "MODEL_OUTPUT_TRUNCATED"),
    (lambda: text("거절", stop="refusal"), "MODEL_REFUSAL"),
    (lambda: text("?", stop="pause_turn"), "MODEL_UNEXPECTED_STOP"),
    (lambda: api_error(), "MODEL_API_ERROR"),
])
async def test_model_stop_reasons(db, first, reason):
    r = await run(db, FakeModel(first()))
    assert r.outcome.reason == reason


async def test_end_turn_nudged_once_then_no_report(db):
    r = await run(db, FakeModel(text("조사 끝"), text("정말 끝")))
    assert r.outcome.reason == "NO_REPORT"


async def test_end_turn_then_submit_after_nudge(db):
    model = FakeModel(text("조사 끝"), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    assert model.requests[1].messages[-1]["content"].startswith("조사를 마쳤다면")


async def test_token_budget_first_request(db):
    r = await run(db, FakeModel(respond(submit(high_report())), count=58_000))   # 58,000 x 1.05 > 60,000
    assert r.outcome.reason == "TOKEN_BUDGET" and r.usage.model_calls == 0


async def test_token_budget_cumulative(db):
    # 매 응답 usage.input_tokens=25,000 → 두 번째 요청 전 spent=25,000, next=25,000 → 51,250 통과,
    # 세 번째 요청 전 spent=50,000 + 26,250 > 60,000 → 중단
    model = FakeModel(respond(history("high", 30), tokens=25_000), respond(history("high", 60), tokens=25_000),
                      respond(submit(high_report())), count=25_000)
    r = await run(db, model)
    assert r.outcome.reason == "TOKEN_BUDGET" and r.usage.model_calls == 2
    assert r.usage.input_tokens == 50_000


async def test_count_tokens_failure_does_not_send(db):
    model = FakeModel(respond(submit(high_report())), count=api_error())
    r = await run(db, model)
    assert r.outcome.reason == "MODEL_API_ERROR" and model.requests == []


async def test_time_limit(db):
    r = await run(db, FakeModel(respond(submit(high_report())), delay=5), total_seconds=3)
    assert r.outcome.reason == "TIME_LIMIT"


@pytest.mark.parametrize("report,reason", [
    (high_report(summary="평균의 １０배 금액입니다."), "REPORT_SCHEMA_INVALID"),                 # V1 유니코드 숫자
    (high_report(summary="평균 결제 금액은 5달러입니다."), "REPORT_SCHEMA_INVALID"),             # V1 (리뷰 사례)
    (high_report(findings=[{"kind": "suspicion", "statement": "{0}",
                            "evidence": [{"evidence_id": "EV-TH-high-30d", "path": "/stats/txn_count", "value": 5}]}]),
     "EVIDENCE_NOT_FOUND"),                                                                   # V2 조회하지 않은 근거
    (high_report(findings=[{"kind": "suspicion", "statement": "{0}",
                            "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/quality/notes/0", "value": "x"}]}]),
     "EVIDENCE_PATH_INVALID"),                                                                # V3 카탈로그 밖 경로
    (high_report(findings=[{"kind": "suspicion", "statement": "{0}",
                            "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/target_category/seen_before", "value": 0}]}]),
     "EVIDENCE_VALUE_MISMATCH"),                                                              # V4 false를 0으로 인용
    (high_report(findings=[{"kind": "suspicion", "statement": "{1}",
                            "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/txn_count", "value": 5}]}]),
     "PLACEHOLDER_INVALID"),                                                                  # V5
    (high_report(summary="평균의 두 배가 넘는 금액입니다."), "NUMBER_WORD_USED"),                 # V7
])
async def test_verification_failures_are_incomplete(db, report, reason):
    # 검증 사유 코드를 보는 테스트이므로 수정 요청을 끕니다 (수정 요청은 아래 #38 테스트)
    r = await run(db, FakeModel(respond(submit(report))), max_report_repairs=0)
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", reason)
    assert r.report == report and r.rendered is None
    assert "verification" in step_types(r) and r.first_submission_ok is False


async def test_trace_is_serializable_and_has_no_secrets(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TEST-ONLY-NOT-A-REAL-KEY-7f3e")
    r = await run(db, FakeModel(respond(submit(high_report()))))
    blob = json.dumps(r.to_dict(), ensure_ascii=False)
    assert "TEST-ONLY-NOT-A-REAL-KEY-7f3e" not in blob and "SYNTHETIC-" not in blob and "is_fraud" not in blob
    assert r.run_id.startswith("run_") and len(r.run_id) == 30


# ---------- PR #27 리뷰 회귀 ----------

import asyncio
from contextlib import asynccontextmanager

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from fds_investigator.agent.tool_session import ToolCallResult


class FailingAfter:
    """실제 툴 세션을 감싸, n번째 호출부터 MCP 프로토콜 오류를 던집니다."""

    def __init__(self, inner, fail_from_call: int):
        self.inner, self.n, self.fail_from = inner, 0, fail_from_call
        self.tools = inner.tools

    async def call(self, name, args) -> ToolCallResult:
        self.n += 1
        if self.n >= self.fail_from:
            raise McpError(ErrorData(code=-32603, message="transport broken"))
        return await self.inner.call(name, args)


def failing_session(db, fail_from_call):
    @asynccontextmanager
    async def opener(target):
        async with McpToolSession(target, db) as s:
            yield FailingAfter(s, fail_from_call)
    return opener


@pytest.mark.parametrize("fail_from,stage", [(1, "baseline_tool_call"), (2, "tool_call")])
async def test_mcp_protocol_error_becomes_tool_server_failure_with_trace(db, fail_from, stage):
    model = FakeModel(respond(history("high", 30)), respond(submit(high_report())))
    r = await investigate("high", model, failing_session(db, fail_from), MODEL, Limits())
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", "TOOL_SERVER_FAILURE")
    assert r.outcome.detail["stage"] == stage
    assert r.steps[-1]["status"] == "session_failure"          # 부분 기록이 남음
    json.dumps(r.to_dict(), ensure_ascii=False)                 # 직렬화 가능


async def test_slow_tool_server_start_is_time_limit():
    cleaned = []

    @asynccontextmanager
    async def slow(target):
        try:
            await asyncio.sleep(5)
            yield None
        finally:
            cleaned.append(True)

    r = await investigate("high", FakeModel(), slow, MODEL, Limits(total_seconds=0.5))
    assert r.outcome.reason == "TIME_LIMIT" and r.outcome.detail == "tool_server_start"
    assert cleaned == [True]                                    # 취소되어도 정리됨


async def test_tool_server_refusing_start_is_failure_and_cleaned(db):
    # 허용 목록 형식 오류 → 툴 서버가 시작을 거부(종료 코드 1) → initialize 실패 → 세션이 스스로 정리
    r = await run(db, FakeModel(), target="bad;id")
    assert r.outcome.reason == "TOOL_SERVER_FAILURE"


async def test_duplicates_within_response_and_default_args_are_normalized(db):
    # 같은 30일 조회 두 번 + lookback 생략(=기본 90일, 기본 증거와 같음) + 90.0
    a, b = history("high", 30), history("high", 30)
    c = history("high")
    d = history("high", 90)
    d["input"]["lookback_days"] = 90.0
    model = FakeModel(respond(a, b, c, d), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    blocks = model.requests[1].messages[-1]["content"]
    assert [x["tool_use_id"] for x in blocks] == [a["id"], b["id"], c["id"], d["id"]]   # 응답 순서 그대로
    assert [x["is_error"] for x in blocks] == [False, True, True, True]
    assert [json.loads(x["content"]).get("error", {}).get("code") for x in blocks] == [
        None, "DUPLICATE_CALL", "DUPLICATE_CALL", "DUPLICATE_CALL"]
    assert r.usage.tool_calls == 1 and r.usage.rejected_calls == 3   # 실제 실행은 1회


async def test_limit_counts_unique_requests_only(db):
    model = FakeModel(respond(history("high", 30), history("high", 30)), respond(submit(high_report())))
    r = await run(db, model, max_tool_calls=1)
    codes = [json.loads(x["content"]).get("error", {}).get("code") for x in model.requests[1].messages[-1]["content"]]
    assert codes == [None, "DUPLICATE_CALL"] and r.usage.tool_calls == 1


async def test_unexpected_client_exception_is_model_api_error(db):
    r = await run(db, FakeModel(RuntimeError("boom")))
    assert r.outcome.reason == "MODEL_API_ERROR" and r.outcome.detail["error"] == "RuntimeError"


async def test_any_other_exception_is_recorded_not_raised():
    @asynccontextmanager
    async def broken(target):
        class S:
            tools = [{"description": "name 필드 없음"}]
            async def call(self, *a): return ToolCallResult({"status": "ok", "evidence_id": "EV-TH-x-90d"}, False)
        yield S()

    r = await investigate("x", FakeModel(), broken, MODEL, Limits())
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", "AGENT_INTERNAL_ERROR")


# ---------- 검증 실패 시 1회 수정 요청 (#38, docs/04 4.4) ----------

def _no_placeholder() -> dict:
    return high_report(findings=[{"kind": "suspicion", "statement": "대상 금액이 최댓값을 넘습니다.",
                                  "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/target_exceeds_max",
                                                "value": True}]}])


async def test_failed_report_is_repaired_once_and_reported(db):
    bad = submit(_no_placeholder())
    model = FakeModel(respond(bad), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    assert r.first_submission_ok is False                       # 첫 제출 지표는 실패로 남음
    assert (r.usage.report_submissions, r.usage.report_repairs, r.usage.model_calls) == (2, 1, 2)
    assert r.report == high_report()                            # 렌더링은 수정본 기준
    # 수정 요청은 첫 submit_report에 대한 is_error tool_result이며, 실패 목록만 담고 고친 문장은 제안하지 않음
    block = model.requests[1].messages[-1]["content"][0]
    assert block["tool_use_id"] == bad["id"] and block["is_error"] is True
    payload = json.loads(block["content"])
    assert set(payload) == {"status", "error", "failures"}
    assert payload["failures"][0]["code"] == "EVIDENCE_NOT_REFERENCED"
    assert step_types(r).count("verification") == 2 and "repair_requested" in step_types(r)


async def test_second_failure_ends_incomplete_with_its_own_code(db):
    second = high_report(findings=[{"kind": "suspicion", "statement": "대상은 {5}입니다.",
                                    "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/target_exceeds_max",
                                                  "value": True}]}])
    r = await run(db, FakeModel(respond(submit(_no_placeholder())), respond(submit(second))))
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", "PLACEHOLDER_INVALID")
    assert r.usage.report_repairs == 1 and r.first_submission_ok is False


async def test_first_pass_report_needs_no_repair(db):
    r = await run(db, FakeModel(respond(submit(high_report()))))
    assert r.outcome.status == "reported" and r.first_submission_ok is True
    assert (r.usage.report_submissions, r.usage.report_repairs) == (1, 0)


async def test_repair_still_obeys_model_call_limit(db):
    # 수정 요청은 예약되었지만 호출 상한에 걸려 모델은 피드백을 받지 못함 → 실제 수정 요청 0회 (PR #39 리뷰)
    r = await run(db, FakeModel(respond(submit(_no_placeholder()))), max_model_calls=1)
    assert r.outcome.reason == "MAX_MODEL_CALLS" and r.usage.report_repairs == 0
    assert "repair_scheduled" in step_types(r) and "repair_requested" not in step_types(r)


async def test_model_may_look_up_more_evidence_after_repair_request(db):
    fixed = high_report(findings=[{"kind": "gap", "statement": "장기 기간은 {0}입니다.",
                                   "evidence": [{"evidence_id": "EV-TH-high-365d", "path": "/stats/target_exceeds_max",
                                                 "value": False}]}], recommendation="insufficient_evidence")
    model = FakeModel(respond(submit(_no_placeholder())), respond(history("high", 365)), respond(submit(fixed)))
    r = await run(db, model)
    assert r.outcome.status == "reported", r.outcome
    assert set(r.tool_results) == {"EV-TH-high-90d", "EV-TH-high-365d"}


async def test_no_submission_leaves_first_pass_unknown(db):
    r = await run(db, FakeModel(text("생각 중"), text("아직")))
    assert r.outcome.reason == "NO_REPORT" and r.first_submission_ok is None


async def test_repair_request_lists_all_failures_and_asks_for_targeted_fix(db):
    # 요약 숫자 + 근거 미참조가 한 번에 전달되어야 함 (27B r1: 요약 오류만 전달 → 다른 곳에서 재실패)
    bad = _no_placeholder()
    bad["summary"] = "평균의 10배입니다."
    model = FakeModel(respond(submit(bad)), respond(submit(high_report())))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    payload = json.loads(model.requests[1].messages[-1]["content"][0]["content"])
    assert {f["code"] for f in payload["failures"]} == {"REPORT_SCHEMA_INVALID", "EVIDENCE_NOT_REFERENCED"}
    assert "위치만 고치고" in payload["error"]["message"]



# ---------- PR #39 리뷰 ----------

async def test_repair_payload_does_not_echo_model_text_or_values(db):
    # Codex 재현: 요약의 '123 SYNTHETIC_MARKER'가 수정 요청에 그대로 되돌아감 → 코드·위치·고정 안내만
    bad = high_report(summary="123 SYNTHETIC_MARKER", findings=[
        {"kind": "suspicion", "statement": "SYNTHETIC_MARKER {0}",
         "evidence": [{"evidence_id": "EV-TH-high-90d", "path": "/stats/target_exceeds_max", "value": True},
                      {"evidence_id": "EV-TH-high-90d", "path": "/stats/txn_count", "value": 999}]}])
    model = FakeModel(respond(submit(bad)), respond(submit(high_report())))
    r = await run(db, model)
    raw = model.requests[1].messages[-1]["content"][0]["content"]
    assert "SYNTHETIC_MARKER" not in raw and "123" not in raw and "999" not in raw
    payload = json.loads(raw)
    assert all(set(f) == {"code", "where", "hint"} for f in payload["failures"])
    # 다음 검증에 필요한 위치는 모두 전달됨, 감사 기록에는 원래 detail이 남음
    audit = next(s for s in r.steps if s["type"] == "verification")["failures"]
    assert {(f["code"], f["where"]) for f in payload["failures"]} == {(f["code"], f["where"]) for f in audit}
    assert any("999" in f["detail"] for f in audit)
    assert r.outcome.status == "reported" and r.usage.report_repairs == 1


async def test_lookups_after_repair_are_counted_separately(db):
    fixed = high_report(findings=[{"kind": "gap", "statement": "장기 기간은 {0}입니다.",
                                   "evidence": [{"evidence_id": "EV-TH-high-365d", "path": "/stats/target_exceeds_max",
                                                 "value": False}]}], recommendation="insufficient_evidence")
    model = FakeModel(respond(history("high", 30)), respond(submit(_no_placeholder())),
                      respond(history("high", 365)), respond(submit(fixed)))
    r = await run(db, model)
    assert r.outcome.status == "reported"
    assert (r.usage.tool_calls, r.usage.repair_tool_calls) == (2, 1)


def test_every_failure_code_has_a_fixed_hint():
    from fds_investigator.agent.prompts import FAILURE_HINTS
    from fds_investigator.agent.verify import FAILURE_CODES
    assert set(FAILURE_HINTS) == set(FAILURE_CODES)


# ---------- #40, Gemini 리뷰 후속 ----------

async def test_rendered_value_comes_from_snapshot_not_citation(db):
    # 27B r2 관찰: 인용 10(정수), 툴 결과 10.0 → V4 통과, 이전에는 '[… 10배]'로 표시
    rep = high_report()
    rep["findings"][0]["evidence"][0]["value"] = 10
    r = await run(db, FakeModel(respond(submit(rep))))
    assert r.outcome.status == "reported"
    assert "10.0배" in r.rendered["findings"][0]["text"] and "10배" not in r.rendered["findings"][0]["text"]
    assert r.report["findings"][0]["evidence"][0]["value"] == 10      # 원본 보고서는 그대로 보존


def test_model_hint_follows_schema_rule_not_location():
    from fds_investigator.agent.prompts import repair_request
    def hint(rule, where="/summary"):
        return repair_request([{"code": "REPORT_SCHEMA_INVALID", "where": where, "detail": "x", "rule": rule}])["failures"][0]["hint"]
    assert "빈 문자열" in hint("minLength")
    assert "형식" in hint("type")
    assert "숫자" in hint("pattern")
    assert "숫자" in hint("pattern", "/findings/0/statement")
    assert "그 밖의" in hint("omitted", "")
    assert "보고서 형식 위반" in hint("required", "/findings/0")
