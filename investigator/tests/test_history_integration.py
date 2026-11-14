"""T1–T15: 실제 stdio MCP와 독립 합성 데이터 기대값을 대조합니다."""

import csv
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fds_investigator.load_csv import load

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"
TOOL = "get_transaction_history"
CASES = json.loads((FIXTURES / "expected_history.json").read_text(encoding="utf-8"))
ALLOWED = ",".join(sorted({"missing"} | {
    case["arguments"]["target_transaction_id"] for case in CASES.values()
}))
NONPUBLIC_COLUMNS = {
    "first": "FixtureGivenName",
    "last": "FixtureFamilyName",
    "street": "FixturePrivateStreet",
    "dob": "1980-04-03",
    "is_fraud": "1",
}

# T11: 연결 시점에 드라이버 예외를 발생시키되, SQLite 저장소의 오류 변환과
# 실제 서버 진입점, stdio 전송은 #11 구현 그대로 실행합니다.
FAIL_CONNECT = """
import sqlite3
from fds_investigator.server import main

def unavailable(*args, **kwargs):
    raise sqlite3.OperationalError("fixture connection failure")

sqlite3.connect = unavailable
main()
"""


def env_for(db: Path, allowed: str | None = ALLOWED) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FDS_", "ANTHROPIC_"))}
    env["FDS_DB_PATH"] = str(db)
    if allowed is not None:
        env["FDS_ALLOWED_TARGETS"] = allowed
    return env


@asynccontextmanager
async def session_for(db: Path, allowed: str = ALLOWED, *, fail_connect: bool = False):
    args = ["-c", FAIL_CONNECT] if fail_connect else ["-m", "fds_investigator.server"]
    params = StdioServerParameters(command=sys.executable, args=args, env=env_for(db, allowed))
    with anyio.fail_after(15):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "fixtures.sqlite"
    load(FIXTURES / "transactions.csv", path)
    return path


def assert_payload(result, *, error: bool = False) -> dict:
    assert result.isError is error
    payload = result.structuredContent
    assert payload is not None
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == payload
    return payload


def assert_expected(payload: dict, expected: dict) -> None:
    assert payload["status"] == expected["status"]
    assert payload["evidence_id"] == expected["evidence_id"]
    assert {key: payload["stats"][key] for key in expected["stats"]} == expected["stats"]
    if "target_category_seen" in expected:
        assert payload["target_category"]["seen_before"] is expected["target_category_seen"]
    if "recent_transaction_ids" in expected:
        assert [row["transaction_id"] for row in payload["recent_transactions"]] == expected[
            "recent_transaction_ids"
        ]
    if "same_second_excluded" in expected:
        n = expected["same_second_excluded"]
        assert any(f"같은 시각의 거래 {n}건" in note for note in payload["quality"]["notes"])
    if "category_distribution" in expected:
        assert payload["category_distribution"] == expected["category_distribution"]
    if payload["status"] == "empty":
        assert payload["recent_transactions"] == payload["category_distribution"] == []
        assert payload["target_category"] == {"seen_before": False, "count": 0, "last_seen_at": None}


@pytest.mark.anyio
async def test_fixture_history_over_stdio(db):
    async with session_for(db) as session:
        tools = (await session.list_tools()).tools
        assert [tool.name for tool in tools] == [TOOL]
        for suffix, published in [("input", tools[0].inputSchema), ("output", tools[0].outputSchema)]:
            contract = ROOT / "contracts" / "mcp" / f"{TOOL}.{suffix}.schema.json"
            assert published == json.loads(contract.read_text(encoding="utf-8"))
        # 하나의 프로세스에서 조회하되, 실패 시 어떤 fixture가 깨졌는지 표시합니다.
        for name, case in CASES.items():
            result = await session.call_tool(TOOL, case["arguments"])
            try:
                assert_expected(assert_payload(result), case["expected"])
            except AssertionError as exc:
                raise AssertionError(f"fixture {name}: {exc}") from exc


@pytest.mark.anyio
@pytest.mark.parametrize(("args", "code"), [
    ({"target_transaction_id": "missing"}, "NOT_FOUND"),
    ({"target_transaction_id": "otherdecoy"}, "OUT_OF_SCOPE"),
    ({"target_transaction_id": "neverexists"}, "OUT_OF_SCOPE"),
    ({"target_transaction_id": "high", "lookback_days": 400}, "INVALID_ARGUMENT"),
], ids=["T8", "T9-existing", "T9-missing", "T10"])
async def test_structured_request_errors(db, args, code):
    async with session_for(db) as session:
        payload = assert_payload(await session.call_tool(TOOL, args), error=True)
        assert payload["status"] == "error"
        assert "evidence_id" not in payload
        assert payload["error"]["code"] == code
        assert payload["error"]["retryable"] is False


@pytest.mark.anyio
async def test_t11_connection_failure_is_not_empty(db):
    async with session_for(db, "new") as session:
        args = {"target_transaction_id": "new"}
        assert assert_payload(await session.call_tool(TOOL, args))["status"] == "empty"
    async with session_for(db, "new", fail_connect=True) as session:
        result = await session.call_tool(TOOL, args)
        payload = assert_payload(result, error=True)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "DATA_SOURCE_UNAVAILABLE"
        assert payload["error"]["retryable"] is True
        assert "fixture connection failure" not in result.content[0].text
        assert "stats" not in payload


@pytest.mark.anyio
async def test_t12_nonpublic_values_do_not_reach_mcp(tmp_path):
    # 원본 fixture를 바꾸지 않고 임시 CSV에만 합성 비노출 필드를 넣습니다.
    source = tmp_path / "input.csv"
    with (FIXTURES / "transactions.csv").open(encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        assert reader.fieldnames is not None
        with source.open("w", encoding="utf-8", newline="") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=[*reader.fieldnames, *NONPUBLIC_COLUMNS])
            writer.writeheader()
            writer.writerows(row | NONPUBLIC_COLUMNS for row in reader)

    path = tmp_path / "nonpublic.sqlite"
    load(source, path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(transactions)")}
    assert columns.isdisjoint({"cc_num", *NONPUBLIC_COLUMNS})

    async with session_for(path, "high") as session:
        result = await session.call_tool(TOOL, {"target_transaction_id": "high"})
        payload = assert_payload(result)
        assert payload["status"] == "ok"
        for forbidden in (
            "cc_num", "is_fraud", "SYNTHETIC-", NONPUBLIC_COLUMNS["first"],
            NONPUBLIC_COLUMNS["last"], NONPUBLIC_COLUMNS["street"], NONPUBLIC_COLUMNS["dob"],
        ):
            assert forbidden not in result.content[0].text
            assert forbidden not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.anyio
async def test_t14_share_rounds_to_zero_over_stdio(tmp_path):
    subprocess.run(
        [sys.executable, str(FIXTURES / "generate.py"), "--include-large", "--out", str(tmp_path)],
        check=True, timeout=15,
    )
    path = tmp_path / "large.sqlite"
    load(tmp_path / "transactions.csv", path)
    case = json.loads((tmp_path / "expected_history.json").read_text(encoding="utf-8"))["T14_share"]
    async with session_for(path, "share") as session:
        result = await session.call_tool(TOOL, case["arguments"])
        assert_expected(assert_payload(result), case["expected"])


@pytest.mark.parametrize("allowed", [None, "", "  ", "high,bad-id", "*"],
                         ids=["missing", "empty", "blank", "malformed", "wildcard-no-env"])
def test_t15_invalid_scope_refuses_startup(db, allowed):
    result = subprocess.run(
        [sys.executable, "-m", "fds_investigator.server"],
        env=env_for(db, allowed), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    assert "시작 거부" in result.stderr
    assert "FDS_ALLOWED_TARGETS" in result.stderr
    assert result.stdout == ""
