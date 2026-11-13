"""Regenerate deterministic synthetic Sparkov-format fixtures (stdlib only)."""
import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
PARSER.add_argument("--include-large", action="store_true", help="include the 20,001-row T14 share case")
ARGS = PARSER.parse_args()
BASE = ARGS.out
BASE.mkdir(parents=True, exist_ok=True)
AS_OF = datetime(2020, 6, 21, 12)
ROWS = []
LABELS = []
CASES = {}


def add(txid, card, amount, at=AS_OF, category="grocery_pos", lat="40.71", lon="-74.00", label=None):
    ROWS.append((at.strftime("%Y-%m-%d %H:%M:%S"), f"SYNTHETIC-{card}", "fraud_Synthetic Shop",
                 category, amount, txid, lat, lon))
    if label is not None:
        LABELS.append((txid, label))


def history(card, amounts, category="grocery_pos"):
    for i, amount in enumerate(amounts, 1):
        add(f"{card}h{i:05}", card, amount, AS_OF - timedelta(seconds=i), category)


def expect(name, ids, count, mean, status, **fields):
    args = {"target_transaction_id": ids}
    if "lookback_days" in fields:
        args["lookback_days"] = fields.pop("lookback_days")
    days = args.get("lookback_days", 90)
    CASES[name] = {"arguments": args, "expected": {
        "status": status, "evidence_id": f"EV-TH-{ids}-{days}d",
        "stats": {"txn_count": count, "amount_mean": mean, **fields.pop("stats", {})}, **fields,
    }}


# T1/T5/T6/T7: five eligible rows incl. exact lower boundary; four exclusions.
history("high", ["10.00", "20.00", "30.00", "40.00"])
add("highlower", "high", "50.00", AS_OF - timedelta(days=90))
add("higholder", "high", "900.00", AS_OF - timedelta(days=90, seconds=1))
add("highsame", "high", "800.00")
add("highfuture", "high", "700.00", AS_OF + timedelta(seconds=1))
add("otherdecoy", "other", "600.00", AS_OF - timedelta(seconds=3))
add("high", "high", "300.00", category="shopping_net", label=1)
expect("T1_T5_T6_T7_T12", "high", 5, "30.00", "ok",
       stats={"amount_median": "30.00", "amount_p95": None, "amount_max": "50.00",
              "target_to_mean_ratio": 10, "target_exceeds_max": True},
       recent_transaction_ids=["highh00001", "highh00002", "highh00003", "highh00004", "highlower"],
       same_second_excluded=1, target_category_seen=False)
expect("window7", "high", 4, "25.00", "insufficient", lookback_days=7)
history("usual", ["20.00", "24.00", "25.00", "26.00", "30.00"])
add("usual", "usual", "25.00", label=0)
expect("T2", "usual", 5, "25.00", "ok", stats={"target_to_mean_ratio": 1, "target_exceeds_max": False},
       target_category_seen=True)
history("sparse", ["1.00", "2.00", "3.00"])
add("sparse", "sparse", "9.00", label=0)
expect("T3", "sparse", 3, "2.00", "insufficient", stats={"amount_p95": None})
add("new", "new", "12.00", label=0)
expect("T4", "new", 0, None, "empty")
for n, mean, p95 in [(4, "2.50", None), (5, "3.00", None), (19, "10.00", None), (20, "10.50", "19.00")]:
    key = f"edge{n}"
    history(key, [f"{i}.00" for i in range(1, n + 1)])
    add(key, key, "20.00")
    expect(f"T13_{n}", key, n, mean, "ok" if n >= 5 else "insufficient", stats={"amount_p95": p95})
for key, amounts, ratio in [("zero", ["0.00"] * 5, None), ("tiny", ["0.00"] * 4 + ["0.01"], 500)]:
    history(key, amounts)
    add(key, key, "1.00")
    expect(f"T14_{key}", key, 5, "0.00", "ok", stats={"target_to_mean_ratio": ratio})
# 20,001 is the smallest count for which one occurrence rounds to 0 at 4 dp.
if ARGS.include_large:
    history("share", ["1.00"] * 20000)
    add("sharerare", "share", "1.00", AS_OF - timedelta(days=1), "gas_transport")
    add("share", "share", "1.00")
    expect("T14_share", "share", 20001, "1.00", "ok", stats={"amount_p95": "1.00"},
           category_distribution=[{"category": "grocery_pos", "count": 20000, "share": 1.0},
                                  {"category": "gas_transport", "count": 1, "share": 0.0}])

# New York to Los Angeles: adjacent POS merchant coordinates, W3 fixture.
add("geoprior", "geo", "20.00", AS_OF - timedelta(minutes=10))
add("geo", "geo", "22.00", lat="34.05", lon="-118.24")
expect("geo_history", "geo", 1, "20.00", "insufficient")

for file, header, rows in [
    ("transactions.csv", ("trans_date_trans_time", "cc_num", "merchant", "category", "amt", "trans_num", "merch_lat", "merch_long"), ROWS),
    ("evaluation_labels.csv", ("trans_num", "is_fraud"), LABELS),
]:
    with (BASE / file).open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)

with (BASE / "expected_history.json").open("w", encoding="utf-8") as f:
    json.dump(CASES, f, indent=2, ensure_ascii=False)
    f.write("\n")
