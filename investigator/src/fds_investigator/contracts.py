"""contracts/mcp의 JSON Schema를 읽고 검증기를 만듭니다. 스키마가 계약의 기준입니다."""

import json
import os
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

TOOL_NAME = "get_transaction_history"   # 기본 툴 (이전 호출부 호환)
GEO_TOOL = "compute_geo_anomaly"
MERCHANT_TOOL = "get_merchant_profile"
TOOL_NAMES = (TOOL_NAME, GEO_TOOL, MERCHANT_TOOL)


def contracts_dir() -> Path:
    override = os.environ.get("FDS_CONTRACTS_DIR")
    if override:
        return Path(override)
    # investigator/src/fds_investigator/contracts.py → 저장소 루트/contracts/mcp
    return Path(__file__).resolve().parents[3] / "contracts" / "mcp"


@cache
def load_schema(kind: str, tool: str = TOOL_NAME) -> dict:
    path = contracts_dir() / f"{tool}.{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def input_validator(tool: str = TOOL_NAME) -> Draft202012Validator:
    return Draft202012Validator(load_schema("input", tool))


@cache
def output_validator(tool: str = TOOL_NAME) -> Draft202012Validator:
    return Draft202012Validator(load_schema("output", tool))


def schema_version(tool: str = TOOL_NAME) -> str:
    return load_schema("output", tool)["properties"]["schema_version"]["const"]
