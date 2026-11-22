"""get_merchant_profile (#56): 모집단 구분, 최소 익명성, 통계, 출력 계약, 검사 순서, stdio."""

import dataclasses
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import BASE, FakeRepo, txn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fds_investigator import contracts
from fds_investigator.config import AllowList
from fds_investigator.load_csv import load
from fds_investigator.merchant import (
    MIN_CARDS,
    build_merchant_result,
    split_merchant_window,
)
from fds_investigator.service import get_merchant_profile

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
TOOL = contracts.MERCHANT_TOOL
START = BASE - timedelta(days=90)


def m(t, merchant="Hub"):
    return dataclasses.replace(t, merchant_name=merchant)


TARGET = m(txn("T0", "850.00", at=BASE))


def valid(out: dict) -> dict:
    errors = list(contracts.output_validator(TOOL).iter_errors(out))
    assert not errors, [e.message for e in errors]
    return out


def others(n_cards: int, per_card: int = 2, amount: str = "800.00") -> list:
    return [m(txn(f"O{c}{k}", amount, at=BASE - timedelta(days=c + 1, hours=k), card=f"card_o{c}"))
            for c in range(n_cards) for k in range(per_card)]


# ---------- 모집단 구분 ----------

def test_split_separates_other_cards_from_own_and_enforces_window():
    own = m(txn("A", "10.00", at=BASE - timedelta(days=1)))
    other = m(txn("B", "20.00", at=BASE - timedelta(days=2), card="card_b"))
    rows = [TARGET, own, other,
            m(txn("S", "1.00", at=BASE, card="card_b")),                               # 같은 초
            m(txn("X", "1.00", at=START - timedelta(seconds=1), card="card_b")),         # 기간 밖
            m(txn("E", "1.00", at=START, card="card_b")),                                # 하한 포함
            m(txn("L", "1.00", at=BASE + timedelta(seconds=1), card="card_b")),          # 이후
            m(txn("D", "1.00", at=BASE - timedelta(days=1), card="card_b"), "Other")]   # 다른 가맹점
    o, mine = split_merchant_window(TARGET, rows, START)
    assert sorted(t.transaction_id for t in o) == ["B", "E"] and [t.transaction_id for t in mine] == ["A"]


# ---------- 통계와 최소 익명성 ----------

def test_ok_result_compares_target_with_other_cards():
    rows = others(3, 2, "800.00") + [m(txn("O9", "900.00", at=BASE - timedelta(days=9), card="card_o9"))]
    out = valid(build_merchant_result(TARGET, rows, [], 90, "1.0"))
    st = out["merchant_stats"]
    assert out["status"] == "ok" and (st["txn_count"], st["card_count"]) == (7, 4)
    assert st["amount_max"] == "900.00" and st["target_exceeds_max"] is False
    assert st["target_to_mean_ratio"] == pytest.approx(850 / (800 * 6 + 900) * 7, abs=0.01)
    assert out["card_at_merchant"] == {"seen_before": False, "count": 0, "last_seen_at": None}
    assert out["evidence_id"] == "EV-MP-T0-90d"


@pytest.mark.parametrize("n_cards,per_card,status", [
    (2, 5, "insufficient"),     # 거래 10건이어도 카드 2장 → 금액 비공개 (익명성 기준)
    (3, 1, "insufficient"),     # 카드 3장이어도 거래 3건 → 표본 기준 미달
    (0, 0, "empty"),
])
def test_amounts_are_suppressed_below_sample_or_card_threshold(n_cards, per_card, status):
    out = valid(build_merchant_result(TARGET, others(n_cards, per_card), [], 90, "1.0"))
    st = out["merchant_stats"]
    assert out["status"] == status and st["txn_count"] == n_cards * per_card and st["card_count"] == n_cards
    assert all(st[k] is None for k in ("amount_mean", "amount_median", "amount_max", "target_to_mean_ratio",
                                       "target_exceeds_max"))
    assert MIN_CARDS == 3


def test_all_zero_amounts_make_ratio_undefined():
    out = valid(build_merchant_result(TARGET, others(3, 2, "0.00"), [], 90, "1.0"))
    assert out["merchant_stats"]["target_to_mean_ratio"] is None and out["merchant_stats"]["target_exceeds_max"] is True


def test_own_history_at_merchant_is_reported_even_when_others_are_sparse():
    own = [m(txn("A1", "10.00", at=BASE - timedelta(days=3))), m(txn("A2", "12.00", at=BASE - timedelta(days=1)))]
    out = valid(build_merchant_result(TARGET, [], own, 90, "1.0"))
    assert out["status"] == "empty"
    assert out["card_at_merchant"] == {"seen_before": True, "count": 2, "last_seen_at": "2020-06-20T02:13:44"}


# ---------- 출력 계약 ----------

@pytest.mark.parametrize("mutate", [
    lambda o: o["merchant_stats"].update(amount_mean="800.00"),                   # insufficient인데 금액 공개
    lambda o: o.update(card_tokens=["card_o0"]),                                  # 카드 토큰 노출 금지
    lambda o: o["merchant_stats"].update(cards=["card_o0"]),
    lambda o: o["card_at_merchant"].update(seen_before=True),                     # 건수 0인데 이용 이력 있음
    lambda o: o["basis"].update(min_cards=1),                                     # 기준은 계약에 고정
    lambda o: o.update(fraud_rate=0.1),                                           # 라벨 기반 지표 금지
], ids=["leak-amount", "card-tokens", "nested-cards", "seen-inconsistent", "min-cards", "fraud-rate"])
def test_output_contract_rejects(mutate):
    out = build_merchant_result(TARGET, others(2, 5), [], 90, "1.0")
    mutate(out)
    assert list(contracts.output_validator(TOOL).iter_errors(out))


# ---------- 서비스 ----------

async def call(args, repo, allow=None):
    allow = allow or AllowList(ids=frozenset(), allow_all=True)
    return valid(await get_merchant_profile(args, allow, repo, 5.0))


async def test_scope_before_existence_and_invalid_arguments():
    repo = FakeRepo([TARGET])
    assert (await call({"target_transaction_id": "x"}, repo, AllowList(ids=frozenset({"T0"}))))["error"]["code"] == "OUT_OF_SCOPE"
    assert repo.calls == []
    assert (await call({"target_transaction_id": "x"}, repo))["error"]["code"] == "NOT_FOUND"
    for bad in ({}, {"target_transaction_id": "T0", "lookback_days": 6}, {"target_transaction_id": "T0", "lookback_days": 366}):
        assert (await call(bad, repo))["error"]["code"] == "INVALID_ARGUMENT"


async def test_service_uses_merchant_query_and_window():
    rows = [TARGET] + others(3, 2) + [m(txn("OLD", "1.00", at=BASE - timedelta(days=40), card="card_z"))]
    repo = FakeRepo(rows)
    out = await call({"target_transaction_id": "T0", "lookback_days": 30.0}, repo)
    assert out["evidence_id"] == "EV-MP-T0-30d" and out["merchant_stats"]["txn_count"] == 6
    assert repo.calls == ["get", "merchant"]


# ---------- 실제 MCP stdio (합성 fixture) ----------

@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("merchant") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


async def _call(db: Path, target: str) -> dict:
    env = {**os.environ, "FDS_ALLOWED_TARGETS": target, "FDS_DB_PATH": str(db)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "fds_investigator.server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return (await s.call_tool(TOOL, {"target_transaction_id": target})).structuredContent


async def test_fixture_cases_over_stdio(fixture_db):
    typical = await _call(fixture_db, "merchtypical")
    st = typical["merchant_stats"]
    assert typical["status"] == "ok" and (st["txn_count"], st["card_count"]) == (12, 6)
    assert st["target_exceeds_max"] is False and 0.9 < st["target_to_mean_ratio"] < 1.1
    assert typical["merchant"] == {"merchant_name": "Electro Hub", "category": "shopping_pos"}
    rare = await _call(fixture_db, "merchrare")
    assert rare["status"] == "insufficient" and rare["merchant_stats"]["txn_count"] == 5
    assert rare["merchant_stats"]["card_count"] == 2 and rare["merchant_stats"]["amount_mean"] is None
    import re
    assert not re.search(r"card_[0-9a-f]{16}", str(typical) + str(rare))   # 카드 토큰은 결과에 없음
