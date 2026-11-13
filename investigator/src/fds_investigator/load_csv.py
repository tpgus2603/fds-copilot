"""Kaggle Sparkov 형식 CSV를 툴 서버용 SQLite로 적재합니다.

조사 경로에 필요한 필드만 남깁니다. 카드 원번호는 솔트를 섞은 해시 토큰으로 바꾸고,
정답 라벨(is_fraud)과 고객 개인정보(이름, 주소, 생년월일 등)는 적재하지 않습니다.
Sparkov 데이터의 가맹점명에 일괄로 붙은 'fraud_' 접두어는 제거합니다.

사용: python -m fds_investigator.load_csv <csv 경로> <sqlite 경로>
      FDS_CARD_TOKEN_SALT 환경 변수로 솔트를 지정합니다(미지정 시 개발용 고정값).
"""

import csv
import hashlib
import os
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .repository import SCHEMA_SQL, TS_FORMAT

REQUIRED = ("trans_date_trans_time", "cc_num", "merchant", "category", "amt", "trans_num")
DEV_SALT = "fds-dev-salt"


def card_token(cc_num: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{cc_num}".encode()).hexdigest()
    return f"card_{digest[:16]}"


def normalize_merchant(name: str) -> str:
    return name.removeprefix("fraud_")


def load(csv_path: Path, db_path: Path, salt: str = DEV_SALT) -> int:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    count = 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"필수 컬럼이 없습니다: {missing}")
        rows = []
        for r in reader:
            trans_at = datetime.strptime(r["trans_date_trans_time"], "%Y-%m-%d %H:%M:%S")
            rows.append(
                (
                    r["trans_num"],
                    card_token(r["cc_num"], salt),
                    trans_at.strftime(TS_FORMAT),
                    str(Decimal(r["amt"])),
                    r["category"],
                    normalize_merchant(r["merchant"]),
                )
            )
        conn.executemany("INSERT OR REPLACE INTO transactions VALUES (?, ?, ?, ?, ?, ?)", rows)
        count = len(rows)
    conn.commit()
    conn.close()
    return count


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    n = load(Path(sys.argv[1]), Path(sys.argv[2]), os.environ.get("FDS_CARD_TOKEN_SALT", DEV_SALT))
    print(f"{n} rows loaded")


if __name__ == "__main__":
    main()
