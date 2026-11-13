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
    assert cols == ["transaction_id", "card_token", "trans_at", "amount", "category", "merchant_name"]
    row = conn.execute("SELECT * FROM transactions").fetchone()
    assert row == ("abc123", card_token("2703186189652095", "s"), "2019-01-01T00:00:18", "4.97", "misc_net", "Rippin, Kub and Mann")
    assert "2703186189652095" not in row[1]


def test_token_depends_on_salt():
    assert card_token("1", "a") != card_token("1", "b")
    assert card_token("1", "a").startswith("card_")
