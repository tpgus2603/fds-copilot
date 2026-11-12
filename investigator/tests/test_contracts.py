"""contracts/mcp 스키마와 docs/03 명세 예시의 일관성을 검증합니다."""

import copy
import json
import re
from pathlib import Path

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


def test_schemas_are_valid_json_schema():
    Draft202012Validator.check_schema(INPUT)
    Draft202012Validator.check_schema(OUTPUT)


@pytest.mark.parametrize("example", [OK_EXAMPLE, ERROR_EXAMPLE], ids=["ok", "error"])
def test_spec_examples_match_schema(example):
    assert is_valid(OUT_V, example)


def make_empty() -> dict:
    x = copy.deepcopy(OK_EXAMPLE)
    x["status"] = "empty"
    x["stats"] = {k: None for k in x["stats"]} | {"txn_count": 0}
    x["category_distribution"] = []
    x["recent_transactions"] = []
    x["target_category"] = {"seen_before": False, "count": 0, "last_seen_at": None}
    return x


def make_insufficient() -> dict:
    x = copy.deepcopy(OK_EXAMPLE)
    x["status"] = "insufficient"
    x["stats"]["txn_count"] = 3
    x["stats"]["amount_p95"] = None
    return x


def test_empty_and_insufficient_are_valid():
    assert is_valid(OUT_V, make_empty())
    assert is_valid(OUT_V, make_insufficient())


def _mutate(base, fn):
    x = copy.deepcopy(base)
    fn(x)
    return x


@pytest.mark.parametrize(
    "instance",
    [
        _mutate(make_insufficient(), lambda x: x["stats"].__setitem__("amount_p95", "10.00")),
        _mutate(OK_EXAMPLE, lambda x: x["stats"].__setitem__("txn_count", 2)),
        _mutate(OK_EXAMPLE, lambda x: x["stats"].__setitem__("amount_mean", 61.3)),
        _mutate(OK_EXAMPLE, lambda x: x.__setitem__("is_fraud", 1)),
        _mutate(OK_EXAMPLE, lambda x: x["target"].__setitem__("cc_num", "4111111111111111")),
        _mutate(OK_EXAMPLE, lambda x: x.__setitem__("error", ERROR_EXAMPLE["error"])),
        _mutate(ERROR_EXAMPLE, lambda x: x.__setitem__("status", "ok")),
    ],
    ids=[
        "insufficient-with-p95",
        "ok-below-min-sample",
        "amount-as-float",
        "label-leak",
        "pan-leak",
        "ok-with-error",
        "error-labelled-ok",
    ],
)
def test_invalid_outputs_are_rejected(instance):
    assert not is_valid(OUT_V, instance)


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"target_transaction_id": "a1b2c3"}, True),
        ({"target_transaction_id": "a1", "lookback_days": 7}, True),
        ({"target_transaction_id": "a1", "lookback_days": 400}, False),
        ({"target_transaction_id": "a1", "as_of": "2021-01-01T00:00:00"}, False),
        ({"target_transaction_id": "a1;DROP"}, False),
        ({}, False),
    ],
    ids=["minimal", "min-lookback", "lookback-too-long", "as_of-injection", "bad-id", "missing-id"],
)
def test_input_schema(args, expected):
    assert is_valid(IN_V, args) is expected
