"""집계 규칙(명세 3.4, 3.6) 단위 테스트. 기대값은 명세 규칙으로 손으로 계산했습니다."""

from datetime import timedelta
from decimal import Decimal

from conftest import BASE, TARGET, txn
from fds_investigator.history import build_result, median, p95_nearest_rank, split_window


def result(history, same_second=0, target=TARGET, days=90):
    return build_result(target, history, days, same_second, "1.2")


def amounts(*xs):
    return [txn(f"H{i}", x) for i, x in enumerate(xs)]


def test_status_boundaries():
    assert result([])["status"] == "empty"
    assert result(amounts(*["10.00"] * 4))["status"] == "insufficient"
    assert result(amounts(*["10.00"] * 5))["status"] == "ok"


def test_mean_round_half_up():
    # (0.01 + 0.02) / 2 = 0.015 → ROUND_HALF_UP 0.02 (HALF_EVEN이면 0.02, 0.025 사례로도 확인)
    assert result(amounts("0.01", "0.02"))["stats"]["amount_mean"] == "0.02"
    # (0.02 + 0.03) / 2 = 0.025 → HALF_UP 0.03, HALF_EVEN이면 0.02
    assert result(amounts("0.02", "0.03"))["stats"]["amount_mean"] == "0.03"


def test_median_even_uses_average_of_middle_two():
    assert median([Decimal("1.00"), Decimal("2.00"), Decimal("3.00"), Decimal("4.00")]) == Decimal("2.5")
    assert result(amounts("1.00", "2.00", "3.00", "4.00"))["stats"]["amount_median"] == "2.50"


def test_p95_nearest_rank():
    values = [Decimal(i) for i in range(1, 21)]  # 1..20
    assert p95_nearest_rank(values) == Decimal(19)  # ceil(0.95*20) = 19번째
    values = [Decimal(i) for i in range(1, 22)]  # 1..21
    assert p95_nearest_rank(values) == Decimal(20)  # ceil(19.95) = 20번째


def test_p95_only_from_20_samples():
    r19 = result(amounts(*[f"{i}.00" for i in range(1, 20)]))
    r20 = result(amounts(*[f"{i}.00" for i in range(1, 21)]))
    assert r19["stats"]["amount_p95"] is None
    assert r20["stats"]["amount_p95"] == "19.00"


def test_ratio_uses_unrounded_mean():
    # [0.00 x4, 0.01], 대상 1.00 → 평균 0.002 (표시 0.00), 비율 500.00 (명세 3.6 예시)
    target = txn("T0", "1.00", at=BASE)
    r = result(amounts("0.00", "0.00", "0.00", "0.00", "0.01"), target=target)
    assert r["stats"]["amount_mean"] == "0.00"
    assert r["stats"]["target_to_mean_ratio"] == 500.0


def test_ratio_null_when_all_amounts_zero():
    r = result(amounts(*["0.00"] * 5))
    assert r["stats"]["target_to_mean_ratio"] is None
    assert any("평균 금액이 0" in n for n in r["quality"]["notes"])


def test_exceeds_max_is_strict():
    target = txn("T0", "50.00", at=BASE)
    assert result(amounts("50.00", "10.00"), target=target)["stats"]["target_exceeds_max"] is False
    assert result(amounts("49.99", "10.00"), target=target)["stats"]["target_exceeds_max"] is True


def test_share_can_round_to_zero_and_sorting():
    hist = [txn(f"G{i}", "1.00", category="grocery_pos") for i in range(20000)]
    hist.append(txn("X", "1.00", category="travel"))
    dist = result(hist)["category_distribution"]
    # 20000/20001 = 0.99995 → ROUND_HALF_UP 1.0000. 두 비중의 합은 1.0001이 됩니다(명세 3.6).
    assert dist[0] == {"category": "grocery_pos", "count": 20000, "share": 1.0}
    assert dist[1] == {"category": "travel", "count": 1, "share": 0.0}


def test_category_ties_sorted_by_name_and_top_10():
    hist = [txn(f"C{i}", "1.00", category=f"cat{i:02d}") for i in range(12)]
    dist = result(hist)["category_distribution"]
    assert [d["category"] for d in dist] == [f"cat{i:02d}" for i in range(10)]


def test_target_category_within_window():
    hist = [txn("A", "1.00", category="shopping_net", days_before=3), txn("B", "1.00", category="shopping_net", days_before=1)]
    tc = result(hist)["target_category"]
    assert tc == {"seen_before": True, "count": 2, "last_seen_at": (BASE - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")}
    assert result(amounts("1.00"))["target_category"] == {"seen_before": False, "count": 0, "last_seen_at": None}


def test_recent_sorted_desc_then_id_and_limited():
    same = BASE - timedelta(hours=1)
    hist = [txn("b", "1.00", at=same), txn("a", "1.00", at=same)] + [txn(f"z{i}", "1.00", days_before=2 + i) for i in range(12)]
    recent = result(hist)["recent_transactions"]
    assert [r["transaction_id"] for r in recent[:2]] == ["a", "b"]
    assert len(recent) == 10


def test_split_window_excludes_other_card_target_same_second_and_future():
    rows = [
        txn("ok1", "1.00", days_before=1),
        txn("other", "1.00", card="card_b", days_before=1),        # T5
        txn("future", "1.00", at=BASE + timedelta(seconds=1)),     # T6
        txn("same", "1.00", at=BASE),                              # T7
        TARGET,
    ]
    kept, same_second = split_window(TARGET, rows)
    assert [t.transaction_id for t in kept] == ["ok1"]
    assert same_second == 1
    notes = result(kept, same_second=same_second)["quality"]["notes"]
    assert any("같은 시각의 거래 1건" in n for n in notes)


def test_evidence_and_basis():
    r = result(amounts("1.00"), days=30)
    assert r["evidence_id"] == "EV-TH-T0-30d"
    assert r["basis"]["as_of"] == "2020-06-21T02:13:44"
    assert r["basis"]["window_start"] == "2020-05-22T02:13:44"
