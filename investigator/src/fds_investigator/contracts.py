"""contracts/mcp의 JSON Schema를 읽고 검증기를 만듭니다. 스키마가 계약의 기준입니다."""

import json
import os
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

TOOL_NAME = "get_transaction_history"


def contracts_dir() -> Path:
    override = os.environ.get("FDS_CONTRACTS_DIR")
    if override:
        return Path(override)
    # investigator/src/fds_investigator/contracts.py → 저장소 루트/contracts/mcp
    return Path(__file__).resolve().parents[3] / "contracts" / "mcp"


@cache
def load_schema(kind: str) -> dict:
    path = contracts_dir() / f"{TOOL_NAME}.{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def input_validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema("input"))


@cache
def output_validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema("output"))


def schema_version() -> str:
    return load_schema("output")["properties"]["schema_version"]["const"]
