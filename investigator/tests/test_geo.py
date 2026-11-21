"""compute_geo_anomaly (#50): 거리·속도 계산, 직전 거래 경계, 기준 판정, 출력 계약, 검사 순서, stdio."""

import dataclasses
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import BASE, FakeRepo, txn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fds_investigator import contracts
from fds_investigator.config import AllowList
from fds_investigator.errors import DataSourceUnavailable
from fds_investigator.geo import (
    HOME_DISTANCE_THRESHOLD_KM,
    SPEED_THRESHOLD_KMH,
    build_geo_result,
    haversine_km,
    previous_transaction,
)
from fds_investigator.load_csv import load
from fds_investigator.service import compute_geo_anomaly

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
NY, LA = (40.7128, -74.0060), (34.0522, -118.2437)
GEO = contracts.GEO_TOOL


def at(t, lat_lon=NY, home_km=None, **kw):
    return dataclasses.replace(t, merch_lat=lat_lon[0], merch_long=lat_lon[1],
                               home_distance_km=None if home_km is None else Decimal(home_km), **kw)


TARGET = at(txn("T0", "50.00", at=BASE), LA, "3935.7")


def valid(out: dict) -> dict:
    errors = list(contracts.output_validator(GEO).iter_errors(out))
    assert not errors, [e.message for e in errors]
    return out


# ---------- 순수 계산 ----------

def test_haversine_known_distances():
    assert haversine_km(*NY, *NY) == 0
    assert 3930 < haversine_km(*NY, *LA) < 3945          # 뉴욕-로스앤젤레스 약 3,936km
    assert haversine_km(*NY, *LA) == pytest.approx(haversine_km(*LA, *NY))
    assert haversine_km(0, 0, 0, 180) == pytest.approx(3.141592653589793 * 6371.0088)   # 반지구, 수치 안정


def test_previous_transaction_boundaries():
    same_second = at(txn("S", "1.00", at=BASE))
    other_card = at(txn("O", "1.00", at=BASE - timedelta(minutes=1), card="card_b"))
    outside = at(txn("X", "1.00", at=BASE - timedelta(days=30, seconds=1)))
    edge = at(txn("E", "1.00", at=BASE - timedelta(days=30)))
    later = at(txn("L", "1.00", at=BASE + timedelta(seconds=1)))
    near = at(txn("N", "1.00", at=BASE - timedelta(minutes=10)))
    assert previous_transaction(TARGET, [same_second, other_card, outside, edge, later], 30).transaction_id == "E"
    assert previous_transaction(TARGET, [edge, near, same_second], 30).transaction_id == "N"
    assert previous_transaction(TARGET, [same_second, other_card, outside, later], 30) is None


def test_impossible_travel_is_flagged():
    prev = at(txn("P", "20.00", at=BASE - timedelta(minutes=10)), NY)
    out = valid(build_geo_result(TARGET, prev, 30, "1.0"))
    p = out["previous"]
    assert out["status"] == "ok" and p["transaction_id"] == "P" and p["minutes_since"] == 10
    assert 3930 < p["distance_km"] < 3945 and p["speed_kmh"] > 20000 and p["exceeds_plausible_speed"] is True
    assert out["target"]["far_from_home"] is True and out["evidence_id"] == "EV-GA-T0-30d"


def test_plausible_trip_is_not_flagged():
    near = (40.73, -73.99)
    target = at(txn("T1", "24.00", at=BASE), (40.75, -73.98), "4.5")
    prev = at(txn("P", "23.00", at=BASE - timedelta(hours=3)), near)
    out = valid(build_geo_result(target, prev, 30, "1.0"))
    assert out["previous"]["exceeds_plausible_speed"] is False and out["previous"]["speed_kmh"] < 5
    assert out["target"] == {"transaction_id": "T1", "trans_at": "2020-06-21T02:13:44", "merchant_name": "Shop",
                             "home_distance_km": 4.5, "far_from_home": False}


def test_threshold_is_judged_before_rounding():
    # 10시간에 9000.4km → 900.04km/h: 표시값은 900.0이지만 기준(900) 초과로 판정
    start = (0.0, 0.0)
    lon = 9000.4 / (3.141592653589793 * 6371.0088 / 180)
    prev = at(txn("P", "1.00", at=BASE - timedelta(hours=10)), start)
    target = at(txn("T2", "1.00", at=BASE), (0.0, lon))
    p = valid(build_geo_result(target, prev, 30, "1.0"))["previous"]
    assert p["speed_kmh"] == 900 and p["exceeds_plausible_speed"] is True
    assert SPEED_THRESHOLD_KMH == 900 and HOME_DISTANCE_THRESHOLD_KM == 300


@pytest.mark.parametrize("prev,target", [
    (None, TARGET),                                                              # 직전 거래 없음
    (txn("P", "1.00", at=BASE - timedelta(minutes=5)), TARGET),                  # 직전 거래 좌표 없음
    (at(txn("P", "1.00", at=BASE - timedelta(minutes=5))), txn("T0", "1.00", at=BASE)),   # 대상 좌표 없음
], ids=["no-previous", "previous-without-coords", "target-without-coords"])
def test_insufficient_when_speed_cannot_be_computed(prev, target):
    out = valid(build_geo_result(target, prev, 30, "1.0"))
    assert out["status"] == "insufficient" and out["previous"] is None


def test_home_distance_missing_is_null_not_false():
    out = valid(build_geo_result(txn("T0", "1.00", at=BASE), None, 30, "1.0"))
    assert out["target"]["home_distance_km"] is None and out["target"]["far_from_home"] is None


# ---------- 출력 계약 ----------

@pytest.mark.parametrize("mutate", [
    lambda o: o["target"].update(home_lat=40.71),                                # 거주지 좌표 필드 추가 금지
    lambda o: o.update(home={"lat": 40.71}),
    lambda o: o["target"].update(far_from_home=None),                            # 거리 있는데 판정 없음
    lambda o: o.update(status="insufficient"),                                   # previous가 있는데 insufficient
    lambda o: o["basis"].update(speed_threshold_kmh=1000),                       # 기준값은 계약에 고정
    lambda o: o.update(evidence_id="EV-TH-T0-30d"),                              # 다른 툴의 근거 ID 형식
], ids=["target-home-coords", "top-level-home", "far-null", "status-mismatch", "threshold", "evidence-prefix"])
def test_output_contract_rejects(mutate):
    prev = at(txn("P", "20.00", at=BASE - timedelta(minutes=10)), NY)
    out = build_geo_result(TARGET, prev, 30, "1.0")
    mutate(out)
    assert list(contracts.output_validator(GEO).iter_errors(out))


# ---------- 서비스: 검사 순서와 오류 ----------

async def call(args, repo, allow=None):
    allow = allow or AllowList(ids=frozenset(), allow_all=True)
    return valid(await compute_geo_anomaly(args, allow, repo, 5.0))


async def test_scope_is_checked_before_existence():
    repo = FakeRepo([TARGET])
    out = await call({"target_transaction_id": "nope"}, repo, AllowList(ids=frozenset({"T0"})))
    assert out["error"]["code"] == "OUT_OF_SCOPE" and repo.calls == []
    assert (await call({"target_transaction_id": "nope"}, repo))["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("args", [{}, {"target_transaction_id": "a;b"}, {"target_transaction_id": "T0", "lookback_days": 0},
                                  {"target_transaction_id": "T0", "lookback_days": 91}, {"target_transaction_id": "T0", "x": 1}])
async def test_invalid_arguments(args):
    out = await call(args, FakeRepo([TARGET]))
    assert out["error"]["code"] == "INVALID_ARGUMENT" and out["tool"] == GEO


async def test_lookback_is_normalized_and_bounds_previous():
    prev = at(txn("P", "1.00", at=BASE - timedelta(days=10)), NY)
    repo = FakeRepo([TARGET, prev])
    assert (await call({"target_transaction_id": "T0", "lookback_days": 7}, repo))["status"] == "insufficient"
    out = await call({"target_transaction_id": "T0", "lookback_days": 14.0}, repo)
    assert out["status"] == "ok" and out["evidence_id"] == "EV-GA-T0-14d"


async def test_data_source_failure_is_retryable():
    out = await call({"target_transaction_id": "T0"}, FakeRepo([TARGET], raise_on=DataSourceUnavailable("down")))
    assert out["error"] == {"code": "DATA_SOURCE_UNAVAILABLE", "message": "거래 저장소에 연결할 수 없습니다.",
                            "retryable": True}


# ---------- 실제 MCP stdio (합성 fixture) ----------

@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("geo") / "fixtures.sqlite"
    load(ROOT / "data" / "fixtures" / "transactions.csv", path)
    return path


async def _geo_call(db: Path, target: str) -> dict:
    env = {**os.environ, "FDS_ALLOWED_TARGETS": target, "FDS_DB_PATH": str(db)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "fds_investigator.server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool(GEO, {"target_transaction_id": target})
        assert res.isError is (res.structuredContent["status"] == "error")
        return res.structuredContent


async def test_fixture_cases_over_stdio(fixture_db):
    geo = await _geo_call(fixture_db, "geo")
    assert geo["previous"]["transaction_id"] == "geoprior" and geo["previous"]["exceeds_plausible_speed"] is True
    assert geo["target"]["far_from_home"] is True
    near = await _geo_call(fixture_db, "geonear")
    assert near["previous"]["transaction_id"] == "geonearh00001" and near["previous"]["exceeds_plausible_speed"] is False
    assert near["target"]["far_from_home"] is False
    new = await _geo_call(fixture_db, "new")
    assert new["status"] == "insufficient" and new["previous"] is None
    assert "40.71" not in str(geo) and "-74.0" not in str(geo)            # 거주지 좌표는 결과에 없음
