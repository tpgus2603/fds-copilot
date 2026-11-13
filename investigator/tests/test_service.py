"""요청 처리: 검사 순서, 결과 상태, 오류 변환, 출력 계약 준수."""

import pytest

from conftest import TARGET, FakeRepo, txn
from fds_investigator import contracts
from fds_investigator.config import AllowList
from fds_investigator.errors import DataSourceUnavailable
from fds_investigator.service import get_transaction_history

pytestmark = pytest.mark.anyio


def history_rows(n, **kw):
    return [txn(f"H{i}", "10.00", days_before=1 + i, **kw) for i in range(n)]


async def call(args, repo, allow=None, timeout=5.0):
    allow = allow or AllowList(ids=frozenset(), allow_all=True)
    out = await get_transaction_history(args, allow, repo, timeout)
    errors = list(contracts.output_validator().iter_errors(out))
    assert not errors, [e.message for e in errors]  # 모든 결과는 출력 계약을 만족해야 합니다
    return out


@pytest.mark.parametrize("n,status", [(0, "empty"), (3, "insufficient"), (5, "ok"), (42, "ok")])
async def test_statuses(n, status):
    out = await call({"target_transaction_id": "T0"}, FakeRepo([TARGET, *history_rows(n)]))
    assert out["status"] == status
    assert out["stats"]["txn_count"] == n


async def test_default_and_custom_lookback():
    repo = FakeRepo([TARGET, txn("old", "10.00", days_before=100), txn("new", "10.00", days_before=10)])
    assert (await call({"target_transaction_id": "T0"}, repo))["stats"]["txn_count"] == 1
    assert (await call({"target_transaction_id": "T0", "lookback_days": 120}, repo))["stats"]["txn_count"] == 2


@pytest.mark.parametrize(
    "args",
    [{}, {"target_transaction_id": "T0", "lookback_days": 400}, {"target_transaction_id": "a;b"},
     {"target_transaction_id": "T0", "as_of": "2021-01-01T00:00:00"}],
    ids=["missing", "lookback-400", "bad-id", "as_of-injection"],
)
async def test_invalid_argument_before_anything_else(args):
    repo = FakeRepo([TARGET])
    out = await call(args, repo, allow=AllowList(ids=frozenset()))  # 허용 목록도 비어 있음
    assert out["error"]["code"] == "INVALID_ARGUMENT"
    assert out["error"]["retryable"] is False
    assert repo.calls == []  # 저장소에 접근하지 않음


async def test_out_of_scope_before_existence_check():
    repo = FakeRepo([TARGET])
    allow = AllowList(ids=frozenset({"OTHER"}))
    existing = await call({"target_transaction_id": "T0"}, repo, allow)          # T9: 존재하지만 범위 밖
    missing = await call({"target_transaction_id": "NOPE"}, repo, allow)         # 존재하지 않고 범위 밖
    assert existing["error"]["code"] == missing["error"]["code"] == "OUT_OF_SCOPE"
    assert repo.calls == []  # 존재 여부를 확인하지 않음


async def test_not_found_inside_scope():
    out = await call({"target_transaction_id": "NOPE"}, FakeRepo([TARGET]), AllowList(ids=frozenset({"NOPE"})))  # T8
    assert out["error"] == {"code": "NOT_FOUND", "message": "대상 거래를 찾을 수 없습니다.", "retryable": False}


async def test_data_source_unavailable_is_not_empty():
    out = await call({"target_transaction_id": "T0"}, FakeRepo([TARGET], raise_on=DataSourceUnavailable("down")))  # T11
    assert out["status"] == "error"
    assert out["error"]["code"] == "DATA_SOURCE_UNAVAILABLE"
    assert out["error"]["retryable"] is True


async def test_timeout():
    out = await call({"target_transaction_id": "T0"}, FakeRepo([TARGET], delay=0.3), timeout=0.05)
    assert out["error"]["code"] == "TIMEOUT"
    assert out["error"]["retryable"] is True


async def test_unexpected_error_is_internal_and_hides_detail():
    out = await call({"target_transaction_id": "T0"}, FakeRepo([TARGET], raise_on=RuntimeError("secret detail")))
    assert out["error"]["code"] == "INTERNAL"
    assert "secret" not in out["error"]["message"]


async def test_scope_control_end_to_end():
    from datetime import timedelta
    from conftest import BASE
    rows = [TARGET, *history_rows(5),
            txn("other", "999.00", card="card_b", days_before=1),
            txn("future", "999.00", at=BASE + timedelta(minutes=1)),
            txn("same", "999.00", at=BASE)]
    out = await call({"target_transaction_id": "T0"}, FakeRepo(rows))
    ids = {r["transaction_id"] for r in out["recent_transactions"]}
    assert ids == {f"H{i}" for i in range(5)}
    assert out["stats"]["amount_max"] == "10.00"


class LeakyRepo(FakeRepo):
    """조회 하한을 무시하는 잘못된 저장소 (MySQL 이관 시 SQL 누락 상황)."""

    def card_history(self, card_token, start, end_inclusive):
        self.calls.append("history")
        return [r for r in self.rows.values() if r.card_token == card_token and r.trans_at <= end_inclusive]


async def test_lower_bound_enforced_even_if_repository_leaks():
    repo = LeakyRepo([TARGET, txn("old", "999.00", days_before=200), txn("new", "10.00", days_before=10)])
    out = await call({"target_transaction_id": "T0"}, repo)
    assert out["stats"]["txn_count"] == 1
    assert out["stats"]["amount_max"] == "10.00"


async def test_integral_float_lookback_is_normalized():
    repo = FakeRepo([TARGET, *history_rows(5)])
    a = await call({"target_transaction_id": "T0", "lookback_days": 90}, repo)
    b = await call({"target_transaction_id": "T0", "lookback_days": 90.0}, repo)
    assert a == b
    assert b["evidence_id"] == "EV-TH-T0-90d"
    assert b["basis"]["lookback_days"] == 90 and isinstance(b["basis"]["lookback_days"], int)
