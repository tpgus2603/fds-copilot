"""실제 MCP stdio 호출 스모크 테스트 (명세 2.2의 오류 반환 경로).

시나리오 전체(T1~T15)의 통합 검증은 #12에서 합니다. 여기서는 #11의 구현 요구사항,
즉 SDK가 아니라 핸들러가 구조화된 오류를 돌려주는지와 시작 거부만 확인합니다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import write_kaggle_csv
from fds_investigator.load_csv import load

pytestmark = pytest.mark.anyio


@pytest.fixture
def db(tmp_path: Path) -> Path:
    rows = [{"trans_date_trans_time": f"2020-06-{d:02d} 10:00:00", "cc_num": "4111000011112222",
             "merchant": "fraud_Lind-Buckridge", "category": "grocery_pos", "amt": "35.10",
             "trans_num": f"h{d}"} for d in range(1, 8)]
    rows.append({"trans_date_trans_time": "2020-06-21 02:13:44", "cc_num": "4111000011112222",
                 "merchant": "fraud_Kuhn LLC", "category": "shopping_net", "amt": "1240.00", "trans_num": "target1"})
    csv_path = write_kaggle_csv(tmp_path / "tx.csv", rows)
    db_path = tmp_path / "tx.db"
    load(csv_path, db_path)
    return db_path


def params(db: Path, allowed: str) -> StdioServerParameters:
    env = {**os.environ, "FDS_ALLOWED_TARGETS": allowed, "FDS_DB_PATH": str(db)}
    return StdioServerParameters(command=sys.executable, args=["-m", "fds_investigator.server"], env=env)


async def test_tools_list_exposes_contract(db):
    async with stdio_client(params(db, "target1")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        assert [t.name for t in tools] == ["get_transaction_history"]
        assert tools[0].inputSchema["properties"]["lookback_days"]["maximum"] == 365
        assert tools[0].outputSchema is not None


async def test_ok_call(db):
    async with stdio_client(params(db, "target1")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("get_transaction_history", {"target_transaction_id": "target1"})
        assert res.isError is False
        sc = res.structuredContent
        assert sc["status"] == "ok" and sc["stats"]["txn_count"] == 7
        assert sc["target"]["merchant_name"] == "Kuhn LLC"           # fraud_ 접두어 제거
        assert json.loads(res.content[0].text) == sc                  # content = structuredContent 직렬화
        blob = res.content[0].text
        for leak in ("is_fraud", "4111000011112222", "Jane", "Main St", "1980-01-01"):
            assert leak not in blob                                    # T12 일부


async def test_invalid_argument_is_structured_not_sdk_text(db):
    async with stdio_client(params(db, "target1")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("get_transaction_history", {"target_transaction_id": "target1", "lookback_days": 400})
        assert res.isError is True
        assert res.structuredContent["error"]["code"] == "INVALID_ARGUMENT"   # T10
        assert "Input validation error" not in res.content[0].text


async def test_out_of_scope(db):
    async with stdio_client(params(db, "someoneelse")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("get_transaction_history", {"target_transaction_id": "target1"})
        assert res.isError is True
        assert res.structuredContent["error"]["code"] == "OUT_OF_SCOPE"


def test_startup_refused_without_allowlist(db):
    env = {k: v for k, v in os.environ.items() if k != "FDS_ALLOWED_TARGETS"} | {"FDS_DB_PATH": str(db)}
    proc = subprocess.run([sys.executable, "-m", "fds_investigator.server"], env=env,
                          capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 1
    assert "시작 거부" in proc.stderr
