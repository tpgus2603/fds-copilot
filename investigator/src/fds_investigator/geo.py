"""compute_geo_anomaly의 계산 규칙 (명세 4절, #50). 순수 함수이며 I/O가 없습니다.

거리는 가맹점 좌표 사이의 대원 거리(haversine)입니다. 가맹점 좌표는 고객의 실측 위치가 아니므로,
"고객이 이동했다"가 아니라 "두 결제가 일어난 가맹점이 이만큼 떨어져 있다"는 사실로만 씁니다.
"""

import math
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from .domain import Transaction

EARTH_RADIUS_KM = 6371.0088          # 평균 지구 반지름 (IUGG)
# 기준값 (사용자 결정, 2026-11-21: 사실 + 고정 기준 불리언). 4주차 Kaggle 적재 후 분포를 보고 다시 정합니다.
SPEED_THRESHOLD_KMH = 900            # 여객기 순항 속도 수준. 이보다 빠르면 두 결제를 한 사람이 연달아 했다고 보기 어려움
HOME_DISTANCE_THRESHOLD_KM = 300     # Sparkov는 가맹점을 거주지 주변(위경도 약 ±1도, 대략 150km 이내)에 생성
DEFAULT_LOOKBACK_DAYS = 30
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
_KM_Q = Decimal("0.1")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def round_km(value: float | Decimal) -> Decimal:
    """소수 첫째 자리 반올림(ROUND_HALF_UP). float를 문자열로 거쳐 이진 표현 오차가 반올림을 바꾸지 않게 합니다."""
    return Decimal(str(value)).quantize(_KM_Q, rounding=ROUND_HALF_UP)


def _num(value: Decimal) -> float | int:
    """JSON 숫자. 정수면 정수로 내 근거 인용 시 10과 10.0 같은 표기 차이를 줄입니다."""
    return int(value) if value == value.to_integral_value() else float(value)


def previous_transaction(target: Transaction, candidates: list[Transaction], lookback_days: int) -> Transaction | None:
    """같은 카드, [as_of - lookback, as_of) 안의 가장 최근 거래. 대상과 같은 초인 거래는 제외합니다(거래 이력 툴과 같은 경계)."""
    start = target.trans_at - timedelta(days=lookback_days)
    kept = [t for t in candidates
            if t.card_token == target.card_token and t.transaction_id != target.transaction_id
            and start <= t.trans_at < target.trans_at]
    return max(kept, key=lambda t: (t.trans_at, t.transaction_id), default=None)


def _has_coords(t: Transaction) -> bool:
    return t.merch_lat is not None and t.merch_long is not None


def build_geo_result(target: Transaction, previous: Transaction | None, lookback_days: int,
                     schema_version: str) -> dict:
    home = target.home_distance_km
    prev_block = None
    if previous is not None and _has_coords(previous) and _has_coords(target):
        minutes = Decimal((target.trans_at - previous.trans_at).total_seconds()) / 60
        km = haversine_km(previous.merch_lat, previous.merch_long, target.merch_lat, target.merch_long)
        speed = Decimal(str(km)) / (minutes / 60)
        prev_block = {
            "transaction_id": previous.transaction_id,
            "trans_at": previous.trans_at.strftime(TS_FORMAT),
            "merchant_name": previous.merchant_name,
            "minutes_since": _num(round_km(minutes)),
            "distance_km": _num(round_km(km)),
            "speed_kmh": _num(round_km(speed)),
            # 기준은 반올림 전 값으로 판정합니다. 표시값 반올림이 판정을 바꾸지 않게 하기 위해서입니다.
            "exceeds_plausible_speed": speed > SPEED_THRESHOLD_KMH,
        }
    return {
        "tool": "compute_geo_anomaly",
        "schema_version": schema_version,
        "status": "ok" if prev_block else "insufficient",
        "evidence_id": f"EV-GA-{target.transaction_id}-{lookback_days}d",
        "basis": {
            "as_of": target.trans_at.strftime(TS_FORMAT),
            "lookback_days": lookback_days,
            "speed_threshold_kmh": SPEED_THRESHOLD_KMH,
            "home_distance_threshold_km": HOME_DISTANCE_THRESHOLD_KM,
        },
        "target": {
            "transaction_id": target.transaction_id,
            "trans_at": target.trans_at.strftime(TS_FORMAT),
            "merchant_name": target.merchant_name,
            "home_distance_km": None if home is None else _num(round_km(home)),
            "far_from_home": None if home is None else home > HOME_DISTANCE_THRESHOLD_KM,
        },
        "previous": prev_block,
    }
