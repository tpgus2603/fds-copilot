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

from .geo import haversine_km, store_km
from .repository import SCHEMA_SQL, TS_FORMAT

REQUIRED = ("trans_date_trans_time", "cc_num", "merchant", "category", "amt", "trans_num")
# 선택 컬럼: 가맹점 좌표(merch_lat, merch_long)와 고객 거주지 좌표(lat, long). 거주지 좌표는 거리 계산에만 쓰고
# 저장하지 않습니다(#50). 개인 위치 정보를 툴 서버 DB에 두지 않으면, 툴 결과로 새어 나갈 경로 자체가 없습니다.
DEV_SALT = "fds-dev-salt"


def card_token(cc_num: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{cc_num}".encode()).hexdigest()
    return f"card_{digest[:16]}"


def normalize_merchant(name: str) -> str:
    return name.removeprefix("fraud_")


def _coord(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


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
            m_lat, m_lon = _coord(r.get("merch_lat")), _coord(r.get("merch_long"))
            h_lat, h_lon = _coord(r.get("lat")), _coord(r.get("long"))
            home_km = (store_km(haversine_km(h_lat, h_lon, m_lat, m_lon))
                       if None not in (m_lat, m_lon, h_lat, h_lon) else None)
            rows.append(
                (
                    r["trans_num"],
                    card_token(r["cc_num"], salt),
                    trans_at.strftime(TS_FORMAT),
                    str(Decimal(r["amt"])),
                    r["category"],
                    normalize_merchant(r["merchant"]),
                    m_lat,
                    m_lon,
                    home_km,
                )
            )
        conn.executemany("INSERT OR REPLACE INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
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
