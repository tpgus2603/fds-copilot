import csv
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from fds_investigator.config import AllowList
from fds_investigator.domain import Transaction

BASE = datetime(2020, 6, 21, 2, 13, 44)


def txn(tid: str, amount: str, *, card: str = "card_a", at: datetime | None = None,
        days_before: float = 1, category: str = "grocery_pos", merchant: str = "Shop") -> Transaction:
    when = at if at is not None else BASE - timedelta(days=days_before)
    return Transaction(tid, card, when, Decimal(amount), category, merchant)


TARGET = txn("T0", "1240.00", at=BASE, category="shopping_net", merchant="Kuhn LLC")


class FakeRepo:
    """저장소 대역. 장애 주입(raise_on)과 지연(delay)을 지원합니다."""

    def __init__(self, rows: list[Transaction], raise_on: Exception | None = None, delay: float = 0):
        self.rows = {r.transaction_id: r for r in rows}
        self.raise_on = raise_on
        self.delay = delay
        self.calls: list[str] = []

    def _maybe_fail(self):
        import time
        if self.delay:
            time.sleep(self.delay)
        if self.raise_on is not None:
            raise self.raise_on

    def get_transaction(self, transaction_id):
        self.calls.append("get")
        self._maybe_fail()
        return self.rows.get(transaction_id)

    def card_history(self, card_token, start, end_inclusive):
        self.calls.append("history")
        self._maybe_fail()
        return [r for r in self.rows.values()
                if r.card_token == card_token and start <= r.trans_at <= end_inclusive]


@pytest.fixture
def allow_all() -> AllowList:
    return AllowList(ids=frozenset(), allow_all=True)


KAGGLE_HEADER = ["", "trans_date_trans_time", "cc_num", "merchant", "category", "amt", "first", "last",
                 "gender", "street", "city", "state", "zip", "lat", "long", "city_pop", "job", "dob",
                 "trans_num", "unix_time", "merch_lat", "merch_long", "is_fraud"]


def write_kaggle_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=KAGGLE_HEADER)
        w.writeheader()
        for i, r in enumerate(rows):
            base = {k: "" for k in KAGGLE_HEADER}
            base.update({"": str(i), "first": "Jane", "last": "Doe", "street": "1 Main St",
                         "dob": "1980-01-01", "is_fraud": "1", "unix_time": "0"})
            base.update(r)
            w.writerow(base)
    return path


@pytest.fixture
def anyio_backend():
    return "asyncio"
