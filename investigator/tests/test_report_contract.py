"""contracts/agent/report.schema.json과 docs/04 보고서 예시의 일관성을 검증합니다."""

import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "agent" / "report.schema.json").read_text(encoding="utf-8"))
SPEC = (ROOT / "docs" / "04_agent_workflow.md").read_text(encoding="utf-8")
EXPECTED = json.loads((ROOT / "data" / "fixtures" / "expected_history.json").read_text(encoding="utf-8"))
V = Draft202012Validator(SCHEMA)
EXAMPLE = json.loads(re.findall(r"```json\n(.*?)```", SPEC, re.S)[0])


def valid(x) -> bool:
    return not list(V.iter_errors(x))


def mutate(fn):
    x = copy.deepcopy(EXAMPLE)
    fn(x)
    return x


def test_schema_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


def test_spec_example_matches_schema():
    assert valid(EXAMPLE)


def test_spec_example_cites_values_consistent_with_fixture():
    """문서 예시가 합성 데이터 'high' 사례의 실제 기대값과 어긋나지 않는지 확인합니다."""
    case = EXPECTED["T1_T5_T6_T7_T12"]["expected"]
    actual = {
        ("EV-TH-high-90d", "/stats/target_to_mean_ratio"): case["stats"]["target_to_mean_ratio"],
        ("EV-TH-high-90d", "/stats/target_exceeds_max"): case["stats"]["target_exceeds_max"],
        ("EV-TH-high-90d", "/stats/txn_count"): case["stats"]["txn_count"],
    }
    assert case["evidence_id"] == "EV-TH-high-90d"
    for f in EXAMPLE["findings"]:
        for e in f["evidence"]:
            assert e["value"] == actual[(e["evidence_id"], e["path"])]


@pytest.mark.parametrize(
    "report",
    [
        mutate(lambda x: x.update(verdict="fraud")),                                    # 판정 필드 금지
        mutate(lambda x: x.update(recommendation="confirmed_fraud")),                   # 권고 값 제한
        mutate(lambda x: x["findings"][0].update(evidence=[])),                         # suspicion 근거 필수
        mutate(lambda x: x["findings"][1].update(evidence=[])),                         # counter 근거 필수
        mutate(lambda x: x["findings"][0].update(kind="opinion")),
        mutate(lambda x: x["findings"][0]["evidence"][0].update(path="stats.ratio")),   # JSON Pointer 아님
        mutate(lambda x: x["findings"][0]["evidence"][0].update(evidence_id="ratio")),
        mutate(lambda x: x["findings"][0]["evidence"][0].pop("value")),                 # 인용 값 필수
        mutate(lambda x: x["findings"][0]["evidence"][0].update(value={"a": 1})),      # 스칼라만
        mutate(lambda x: x.update(findings=[])),
        mutate(lambda x: x.update(summary="")),
        mutate(lambda x: x.update(reasoning="...")),                                    # 내부 추론 필드 금지
    ],
    ids=["verdict-field", "bad-recommendation", "suspicion-no-evidence", "counter-no-evidence", "bad-kind",
         "path-not-pointer", "bad-evidence-id", "missing-value", "non-scalar-value", "no-findings",
         "empty-summary", "reasoning-field"],
)
def test_invalid_reports_rejected(report):
    assert not valid(report)


def test_gap_may_have_no_evidence():
    assert valid(mutate(lambda x: x.update(findings=[{"kind": "gap", "statement": "확인하지 못함", "evidence": []}],
                                           recommendation="insufficient_evidence")))
