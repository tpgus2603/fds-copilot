"""조사 루프 (docs/04): 가짜 모델 + 실제 MCP 툴 서버(합성 데이터)."""

import json
from pathlib import Path

import pytest

from agent_fakes import (FakeModel, api_error, high_report, history, respond, submit, text)
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
    assert r.rendered["findings"][0]["text"] == "대상 금액이 [비교 기간 평균 대비 10.0배]이며 [비교 기간 최댓값 초과]입니다."
    assert r.rendered["findings"][2]["text"] == "비교 표본이 [비교 기간 거래 5건]으로 최소 기준만 충족합니다."
    assert r.rendered["findings"][0]["evidence"][0] == f"{r.run_id}/EV-TH-high-90d#/stats/target_to_mean_ratio"
    assert r.usage.model_calls == 1 and r.usage.tool_calls == 0
    # 첫 요청에 시스템 프롬프트, 조회 툴 + submit_report, 기본 증거가 들어감
    req = model.requests[0]
    assert [t["name"] for t in req.tools] == ["get_transaction_history", "submit_report"]
    assert "EV-TH-high-90d" in req.messages[0]["content"]


async def test_additional_lookup_then_cite_it(db):
    report = high_report(findings=[{"kind": "suspicion", "statement": "짧은 기간에서도 {0}입니다.",
                                    "evidence": [{"evidence_id": "EV-TH-high-7d", "path": "/stats/txn_count", "value": 4}]}])
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
    r = await run(db, FakeModel(respond(submit(report))))
    assert (r.outcome.status, r.outcome.reason) == ("incomplete", reason)
    assert r.report == report and r.rendered is None
    assert "verification" in step_types(r)


async def test_trace_is_serializable_and_has_no_secrets(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TEST-ONLY-NOT-A-REAL-KEY-7f3e")
    r = await run(db, FakeModel(respond(submit(high_report()))))
    blob = json.dumps(r.to_dict(), ensure_ascii=False)
    assert "TEST-ONLY-NOT-A-REAL-KEY-7f3e" not in blob and "SYNTHETIC-" not in blob and "is_fraud" not in blob
    assert r.run_id.startswith("run_") and len(r.run_id) == 30
