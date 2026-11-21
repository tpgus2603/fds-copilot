"""적재 시 라벨·개인정보 제거, 카드 토큰화, 가맹점 접두어 제거."""

import sqlite3

from conftest import write_kaggle_csv
from fds_investigator.load_csv import card_token, load


def test_load_drops_label_and_pii(tmp_path):
    csv_path = write_kaggle_csv(tmp_path / "a.csv", [{
        "trans_date_trans_time": "2019-01-01 00:00:18", "cc_num": "2703186189652095",
        "merchant": "fraud_Rippin, Kub and Mann", "category": "misc_net", "amt": "4.97", "trans_num": "abc123"}])
    db = tmp_path / "a.db"
    assert load(csv_path, db, salt="s") == 1
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    assert cols == ["transaction_id", "card_token", "trans_at", "amount", "category", "merchant_name",
                    "merch_lat", "merch_long", "home_distance_km"]
    row = conn.execute("SELECT * FROM transactions").fetchone()
    assert row == ("abc123", card_token("2703186189652095", "s"), "2019-01-01T00:00:18", "4.97", "misc_net",
                   "Rippin, Kub and Mann", None, None, None)
    assert "2703186189652095" not in row[1]


def test_token_depends_on_salt():
    assert card_token("1", "a") != card_token("1", "b")
    assert card_token("1", "a").startswith("card_")


def test_home_coordinates_become_distance_only(tmp_path):
    # 거주지 좌표(lat, long)는 거리 계산에만 쓰고 저장하지 않음 (#50, 데이터 최소화)
    csv_path = write_kaggle_csv(tmp_path / "g.csv", [{
        "trans_date_trans_time": "2019-01-01 00:00:18", "cc_num": "1", "merchant": "fraud_A", "category": "misc_net",
        "amt": "1.00", "trans_num": "g1", "lat": "40.7128", "long": "-74.0060", "merch_lat": "34.0522",
        "merch_long": "-118.2437"}])
    db = tmp_path / "g.db"
    load(csv_path, db, salt="s")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    assert "lat" not in cols and "long" not in cols
    m_lat, m_lon, home_km = conn.execute("SELECT merch_lat, merch_long, home_distance_km FROM transactions").fetchone()
    assert (m_lat, m_lon) == (34.0522, -118.2437)
    assert 3930 < float(home_km) < 3945                      # 뉴욕-로스앤젤레스 대원 거리 약 3,936km
    dump = "\n".join(conn.iterdump())
    assert "40.7128" not in dump and "-74.006" not in dump   # 거주지 좌표가 DB 어디에도 없음
