"""거래 저장소. 툴 서버는 이 인터페이스로만 데이터를 읽습니다(읽기 전용).

저장소 경계는 장애 주입 지점이기도 합니다(명세 T11).
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .domain import Transaction
from .errors import DataSourceUnavailable

TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


class TransactionRepository(Protocol):
    def get_transaction(self, transaction_id: str) -> Transaction | None: ...

    def card_history(self, card_token: str, start: datetime, end_inclusive: datetime) -> list[Transaction]:
        """start 이상 end_inclusive 이하의 같은 카드 거래. 경계 처리는 호출자가 합니다."""
        ...


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    card_token     TEXT NOT NULL,
    trans_at       TEXT NOT NULL,   -- YYYY-MM-DDTHH:MM:SS, 시간대 없음
    amount         TEXT NOT NULL,   -- 10진수 문자열
    category       TEXT NOT NULL,
    merchant_name  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_card_time ON transactions (card_token, trans_at);
"""


def _row_to_txn(row: sqlite3.Row) -> Transaction:
    return Transaction(
        transaction_id=row["transaction_id"],
        card_token=row["card_token"],
        trans_at=datetime.strptime(row["trans_at"], TS_FORMAT),
        amount=Decimal(row["amount"]),
        category=row["category"],
        merchant_name=row["merchant_name"],
    )


class SqliteTransactionRepository:
    """SQLite 저장소. 읽기 전용 모드(mode=ro)로 열어 쓰기를 원천 차단합니다."""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, check_same_thread=False)
        except sqlite3.Error as e:
            raise DataSourceUnavailable(str(e)) from e
        conn.row_factory = sqlite3.Row
        return conn

    def _query(self, sql: str, params: tuple) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.Error as e:
            raise DataSourceUnavailable(str(e)) from e
        finally:
            conn.close()

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        rows = self._query("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,))
        return _row_to_txn(rows[0]) if rows else None

    def card_history(self, card_token: str, start: datetime, end_inclusive: datetime) -> list[Transaction]:
        rows = self._query(
            "SELECT * FROM transactions WHERE card_token = ? AND trans_at >= ? AND trans_at <= ?",
            (card_token, start.strftime(TS_FORMAT), end_inclusive.strftime(TS_FORMAT)),
        )
        return [_row_to_txn(r) for r in rows]
