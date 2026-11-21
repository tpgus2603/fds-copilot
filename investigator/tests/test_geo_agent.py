"""지리 툴의 조사 루프 통합 (#52): 렌더링 카탈로그, 검증 극성, 중복 판정, 프롬프트. 실제 툴 서버 사용."""

import json
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


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("geoagent") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


def geo_call(target: str, cid: str = "call_geo", **args) -> dict:
    return {"type": "tool_use", "id": cid, "name": "compute_geo_anomaly",
            "input": {"target_transaction_id": target, **args}}


def ev(eid, path, value):
    return {"evidence_id": eid, "path": path, "value": value}


def report(*findings, rec="further_review_recommended"):
    return {"summary": "조사 결과를 요약합니다.", "recommendation": rec, "findings": list(findings)}


async def run(db, target, *script, **limits):
    return await investigate(target, FakeModel(*script), lambda t: McpToolSession(t, db), "fake",
                             Limits(max_report_repairs=0, **limits))


async def test_geo_evidence_is_citable_and_rendered(db):
    rep = report({"kind": "suspicion", "statement": "직전 거래 대비 {0}이며 {1}입니다.",
                  "evidence": [ev("EV-GA-geo-30d", "/previous/speed_kmh", 23616.4),
                               ev("EV-GA-geo-30d", "/previous/exceeds_plausible_speed", True)]})
    r = await run(db, "geo", respond(geo_call("geo")), respond(submit(rep)))
    assert r.outcome.status == "reported", r.outcome
    text = r.rendered["findings"][0]["text"]
    assert text == "직전 거래 대비 [직전 거래 대비 환산 이동 속도 23616.4km/h]이며 [환산 이동 속도 기준 초과]입니다."


async def test_geo_polarity_is_enforced(db):
    r = await run(db, "geo", respond(geo_call("geo")), respond(submit(high_report())))
    snaps = r.tool_results
    as_counter = report({"kind": "counter", "statement": "이동은 {0}입니다.",
                         "evidence": [ev("EV-GA-geo-30d", "/previous/exceeds_plausible_speed", True)]},
                        rec="no_anomaly_found")
    assert verify_report(as_counter, snaps).code == "POLARITY_CONFLICT"
    far_as_counter = report({"kind": "counter", "statement": "거주지와는 {0}입니다.",
                             "evidence": [ev("EV-GA-geo-30d", "/target/far_from_home", True)]}, rec="no_anomaly_found")
    assert verify_report(far_as_counter, snaps).code == "POLARITY_CONFLICT"


async def test_geo_with_sparse_history_can_still_be_suspicion(db):
    # geo 사례: 거래 이력은 insufficient(1건)이지만 지리 근거는 이력과 무관하므로 suspicion으로 인용 가능
    r = await run(db, "geo", respond(geo_call("geo")), respond(submit(high_report())))
    rep = report({"kind": "suspicion", "statement": "이동은 {0}입니다.",
                  "evidence": [ev("EV-GA-geo-30d", "/previous/exceeds_plausible_speed", True)]},
                 {"kind": "gap", "statement": "거래 이력 표본은 {0}입니다.",
                  "evidence": [ev("EV-TH-geo-90d", "/stats/txn_count", 1)]})
    assert verify_report(rep, r.tool_results).ok


async def test_previous_fields_missing_when_insufficient(db):
    # new: 직전 거래 없음 → previous가 null이라 /previous/* 인용은 경로 오류
    r = await run(db, "new", respond(geo_call("new")), respond(submit(high_report())))
    assert r.tool_results["EV-GA-new-30d"]["status"] == "insufficient"
    rep = report({"kind": "gap", "statement": "이동 속도는 {0}입니다.",
                  "evidence": [ev("EV-GA-new-30d", "/previous/speed_kmh", 0)]}, rec="insufficient_evidence")
    assert verify_report(rep, r.tool_results).code == "EVIDENCE_PATH_INVALID"


async def test_geo_default_lookback_is_deduplicated(db):
    model = FakeModel(respond(geo_call("geo", "a"), geo_call("geo", "b", lookback_days=30),
                              geo_call("geo", "c", lookback_days=30.0)), respond(submit(high_report())))
    r = await investigate("geo", model, lambda t: McpToolSession(t, db), "fake", Limits(max_report_repairs=0))
    codes = [json.loads(b["content"]).get("error", {}).get("code") for b in model.requests[1].messages[-1]["content"]]
    assert codes == [None, "DUPLICATE_CALL", "DUPLICATE_CALL"] and r.usage.tool_calls == 1
    assert _canonical("compute_geo_anomaly", {"target_transaction_id": "x"}) == \
        _canonical("compute_geo_anomaly", {"target_transaction_id": "x", "lookback_days": 30})
    assert _canonical("compute_geo_anomaly", {"target_transaction_id": "x"}) != \
        _canonical("get_transaction_history", {"target_transaction_id": "x"})


async def test_out_of_scope_geo_lookup_is_refused_by_tool_server(db):
    r = await run(db, "geo", respond(geo_call("otherdecoy")), respond(submit(high_report())))
    call = next(s for s in r.steps if s["type"] == "tool_call")
    assert call["tool"] == "compute_geo_anomaly" and call["error"]["code"] == "OUT_OF_SCOPE"


def test_render_labels_do_not_claim_customer_location():
    for path, value in [("/previous/distance_km", 3936.1), ("/target/home_distance_km", 3936.1)]:
        text = render_evidence({"evidence_id": "EV-GA-x-30d", "path": path, "value": value})
        assert "가맹점" in text and "고객" not in text and "위치" not in text
    # 지리 툴 경로는 거래 이력의 기간 라벨을 붙이지 않음
    assert render_evidence({"evidence_id": "EV-GA-x-30d", "path": "/previous/speed_kmh", "value": 10}) == \
        "직전 거래 대비 환산 이동 속도 10km/h"


def test_prompt_lists_geo_paths_and_tool_choice_guidance():
    listed = ", ".join(citable_paths())
    assert "/previous/exceeds_plausible_speed" in listed and listed in SYSTEM_PROMPT
    assert "compute_geo_anomaly로 확인합니다" in SYSTEM_PROMPT and "고객의\n   실제 위치가 아닙니다" in SYSTEM_PROMPT
