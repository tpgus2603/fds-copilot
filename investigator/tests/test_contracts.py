"""contracts/mcp 스키마와 docs/03 명세 예시의 일관성을 검증합니다.

PR #20 Codex 리뷰에서 재현된 위반 사례를 회귀 테스트로 고정합니다.
"""

import copy
import json
import re
from pathlib import Path
from typing import Callable

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "mcp"
SPEC = ROOT / "docs" / "03_mcp_tool_spec.md"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


INPUT = load("get_transaction_history.input.schema.json")
OUTPUT = load("get_transaction_history.output.schema.json")
OUT_V = Draft202012Validator(OUTPUT)
IN_V = Draft202012Validator(INPUT)


def spec_examples() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)```", SPEC.read_text(encoding="utf-8"), re.S)
    return [json.loads(b) for b in blocks]


OK_EXAMPLE, ERROR_EXAMPLE = spec_examples()


def is_valid(validator: Draft202012Validator, instance: dict) -> bool:
    return not list(validator.iter_errors(instance))


def mutate(base: dict, fn: Callable[[dict], None]) -> dict:
    x = copy.deepcopy(base)
    fn(x)
    return x


def with_count(n: int, p95: str | None) -> dict:
    """ok 예시를 기준으로 건수와 p95만 바꾼 결과. 상태는 건수에 맞춰 정합니다."""
    x = copy.deepcopy(OK_EXAMPLE)
    x["status"] = "ok" if n >= 5 else "insufficient"
    x["stats"]["txn_count"] = n
    x["stats"]["amount_p95"] = p95
    return x


def make_empty() -> dict:
    x = copy.deepcopy(OK_EXAMPLE)
    x["status"] = "empty"
    x["stats"] = {k: None for k in x["stats"]} | {"txn_count": 0}
    x["category_distribution"] = []
    x["recent_transactions"] = []
    x["target_category"] = {"seen_before": False, "count": 0, "last_seen_at": None}
    return x


def make_error(code: str, retryable: bool) -> dict:
    x = copy.deepcopy(ERROR_EXAMPLE)
    x["error"]["code"] = code
    x["error"]["retryable"] = retryable
    return x


# ---------- 스키마 자체와 명세 예시 ----------


def test_schemas_are_valid_json_schema():
    Draft202012Validator.check_schema(INPUT)
    Draft202012Validator.check_schema(OUTPUT)


@pytest.mark.parametrize("example", [OK_EXAMPLE, ERROR_EXAMPLE], ids=["ok", "error"])
def test_spec_examples_match_schema(example):
    assert is_valid(OUT_V, example)


def test_spec_examples_use_current_schema_version():
    version = OUTPUT["properties"]["schema_version"]["const"]
    assert OK_EXAMPLE["schema_version"] == version
    assert ERROR_EXAMPLE["schema_version"] == version


# ---------- 유효한 결과 ----------


@pytest.mark.parametrize(
    "instance",
    [
        with_count(1, None),
        with_count(4, None),
        with_count(5, None),
        with_count(19, None),
        with_count(20, "180.20"),
        make_empty(),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_mean="0.00", target_to_mean_ratio=None)),
        mutate(OK_EXAMPLE, lambda x: x.update(target_category={"seen_before": True, "count": 3, "last_seen_at": "2020-06-01T10:00:00"})),
        make_error("DATA_SOURCE_UNAVAILABLE", True),
        make_error("TIMEOUT", True),
        make_error("NOT_FOUND", False),
        make_error("INVALID_ARGUMENT", False),
    ],
    ids=[
        "insufficient-1",
        "insufficient-4",
        "ok-5-no-p95",
        "ok-19-no-p95",
        "ok-20-with-p95",
        "empty",
        "mean-zero-ratio-null",
        "category-seen",
        "error-datasource-retryable",
        "error-timeout-retryable",
        "error-notfound",
        "error-invalid-argument",
    ],
)
def test_valid_outputs(instance):
    assert is_valid(OUT_V, instance)


# ---------- 거부되어야 하는 결과 ----------


@pytest.mark.parametrize(
    "instance",
    [
        # 표본 수와 통계의 일관성 (리뷰: output.schema.json:49)
        with_count(19, "180.20"),
        with_count(42, None),
        with_count(20, None),
        with_count(4, "10.00"),
        mutate(with_count(5, None), lambda x: x.update(status="insufficient")),
        mutate(with_count(4, None), lambda x: x.update(status="ok")),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_mean=None)),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_median=None)),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_max=None)),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(target_exceeds_max=None)),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(target_to_mean_ratio=None)),
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_mean="0.00", target_to_mean_ratio=3.1)),
        mutate(OK_EXAMPLE, lambda x: x.update(category_distribution=[])),
        mutate(OK_EXAMPLE, lambda x: x.update(recent_transactions=[])),
        # empty에 이력 증거 (리뷰: output.schema.json:39)
        mutate(make_empty(), lambda x: x.update(category_distribution=OK_EXAMPLE["category_distribution"])),
        mutate(make_empty(), lambda x: x.update(target_category={"seen_before": True, "count": 2, "last_seen_at": "2020-06-01T10:00:00"})),
        mutate(make_empty(), lambda x: x.update(recent_transactions=OK_EXAMPLE["recent_transactions"])),
        mutate(make_empty(), lambda x: x["stats"].update(amount_mean="10.00")),
        # target_category 내부 일관성
        mutate(OK_EXAMPLE, lambda x: x.update(target_category={"seen_before": False, "count": 2, "last_seen_at": None})),
        mutate(OK_EXAMPLE, lambda x: x.update(target_category={"seen_before": True, "count": 0, "last_seen_at": None})),
        # 오류와 정상 데이터 분리 (리뷰: output.schema.json:20)
        mutate(OK_EXAMPLE, lambda x: x.update(status="error", error=ERROR_EXAMPLE["error"])),
        mutate(ERROR_EXAMPLE, lambda x: x.update(evidence_id="EV-TH-a1-90d")),
        mutate(OK_EXAMPLE, lambda x: x.update(error=ERROR_EXAMPLE["error"])),
        mutate(ERROR_EXAMPLE, lambda x: x.update(status="ok")),
        # 오류 코드와 재시도 정책 (리뷰: output.schema.json:153)
        make_error("OUT_OF_SCOPE", True),
        make_error("NOT_FOUND", True),
        make_error("INVALID_ARGUMENT", True),
        make_error("INTERNAL", True),
        make_error("DATA_SOURCE_UNAVAILABLE", False),
        make_error("TIMEOUT", False),
        # 표현과 비노출 필드
        mutate(OK_EXAMPLE, lambda x: x["stats"].update(amount_mean=61.3)),
        mutate(OK_EXAMPLE, lambda x: x.update(is_fraud=1)),
        mutate(OK_EXAMPLE, lambda x: x["target"].update(cc_num="4111111111111111")),
        mutate(OK_EXAMPLE, lambda x: x["recent_transactions"][0].update(is_fraud=0)),
    ],
    ids=[
        "ok-19-with-p95",
        "ok-42-missing-p95",
        "ok-20-missing-p95",
        "insufficient-with-p95",
        "status-insufficient-at-5",
        "status-ok-at-4",
        "ok-missing-mean",
        "ok-missing-median",
        "ok-missing-max",
        "ok-missing-exceeds",
        "ok-ratio-null-with-nonzero-mean",
        "mean-zero-with-ratio",
        "ok-empty-category-distribution",
        "ok-empty-recent",
        "empty-with-categories",
        "empty-category-seen",
        "empty-with-recent",
        "empty-with-mean",
        "category-not-seen-but-count",
        "category-seen-without-date",
        "error-with-success-fields",
        "error-with-evidence-id",
        "ok-with-error",
        "error-labelled-ok",
        "out-of-scope-retryable",
        "not-found-retryable",
        "invalid-argument-retryable",
        "internal-retryable",
        "datasource-not-retryable",
        "timeout-not-retryable",
        "amount-as-float",
        "label-leak-top",
        "pan-leak-target",
        "label-leak-recent",
    ],
)
def test_invalid_outputs_are_rejected(instance):
    assert not is_valid(OUT_V, instance)


# ---------- 입력 ----------


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"target_transaction_id": "a1b2c3"}, True),
        ({"target_transaction_id": "a1", "lookback_days": 7}, True),
        ({"target_transaction_id": "a1", "lookback_days": 365}, True),
        ({"target_transaction_id": "a1", "lookback_days": 6}, False),
        ({"target_transaction_id": "a1", "lookback_days": 400}, False),
        ({"target_transaction_id": "a1", "as_of": "2021-01-01T00:00:00"}, False),
        ({"target_transaction_id": "a1;DROP"}, False),
        ({}, False),
    ],
    ids=["minimal", "min-lookback", "max-lookback", "lookback-too-short", "lookback-too-long", "as_of-injection", "bad-id", "missing-id"],
)
def test_input_schema(args, expected):
    assert is_valid(IN_V, args) is expected
