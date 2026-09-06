import json
import math

import httpx
from fastapi.testclient import TestClient

from errors import AppError
from main import app
from services import search as search_service
from services.search import (
    GeoPoint,
    collect_pois,
    distance_m,
    filter_geocode_candidates,
    midpoint,
    resolve_geocode_point,
    same_place,
)


client = TestClient(app)

EAST_LON = 120.212011
EAST_LAT = 30.291000
BRIDGE_LON = 120.160000
BRIDGE_LAT = 30.260000


def _geocode_item(
    *,
    location: str,
    formatted: str,
    level: str = "兴趣点",
    city: str = "杭州市",
    adcode: str = "330102",
) -> dict:
    return {
        "formatted_address": formatted,
        "province": "浙江省",
        "city": city,
        "district": "上城区",
        "adcode": adcode,
        "location": location,
        "level": level,
        "street": [],
        "number": [],
    }


def _geocode_ok(*items: dict) -> dict:
    return {"status": "1", "info": "OK", "geocodes": list(items)}


def _around_ok(pois: list[dict]) -> dict:
    return {"status": "1", "info": "OK", "pois": pois}


def _poi(*, name: str, address: object, location: str, distance: object) -> dict:
    return {"name": name, "address": address, "location": location, "distance": distance}


def _install_amap(monkeypatch, handler):
    monkeypatch.setattr(search_service.settings, "amap_api_key", "test-key")
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(search_service.httpx, "AsyncClient", fake_client)


def test_midpoint_is_arithmetic_mean_lon_lat():
    lon, lat = midpoint(120.2, 30.1, 120.4, 30.5)
    assert lon == 120.3
    assert lat == 30.3


def test_same_place_requires_adcode_name_and_250m():
    left = GeoPoint(120.0, 30.27, "杭州东站", "杭州东站", "330102", "公交地铁站点")
    close_lon = 120.0 + 200.0 / (111_320 * math.cos(math.radians(30.27)))
    far_lon = 120.0 + 300.0 / (111_320 * math.cos(math.radians(30.27)))
    close = GeoPoint(close_lon, 30.27, "杭州东站A口", "杭州东站A口", "330102", "公交地铁站点")
    far = GeoPoint(far_lon, 30.27, "杭州东站B口", "杭州东站B口", "330102", "公交地铁站点")
    other = GeoPoint(close_lon, 30.27, "城站", "城站", "330102", "公交地铁站点")
    assert same_place(left, close)
    assert not same_place(left, far)
    assert not same_place(left, other)
    assert distance_m(left.longitude, left.latitude, far.longitude, far.latitude) > 250


def test_300m_same_name_stays_ambiguous():
    far_lon = 120.0 + 300.0 / (111_320 * math.cos(math.radians(30.27)))
    items = [
        _geocode_item(
            location="120.0,30.27",
            formatted="浙江省杭州市上城区杭州东站",
            level="公交地铁站点",
        ),
        _geocode_item(
            location=f"{far_lon},30.27",
            formatted="浙江省杭州市上城区杭州东站地铁站",
            level="公交地铁站点",
        ),
    ]
    try:
        resolve_geocode_point(items, city="杭州", address="杭州东站", party_label="第一人")
        assert False, "300m apart should not merge"
    except AppError as exc:
        assert exc.code == "GEOCODE_AMBIGUOUS"


def test_filter_drops_coarse_and_road_levels():
    items = [
        _geocode_item(location="120.2,30.3", formatted="浙江省杭州市西湖区", level="区县"),
        _geocode_item(location="120.2,30.3", formatted="浙江省杭州市延安路", level="道路"),
        _geocode_item(
            location="120.212011,30.291000",
            formatted="浙江省杭州市上城区杭州东站",
            level="公交地铁站点",
        ),
    ]
    kept = filter_geocode_candidates(items, city="杭州", address="杭州东站")
    assert len(kept) == 1
    assert kept[0].longitude == EAST_LON


def test_ambiguous_when_two_clusters_remain():
    items = [
        _geocode_item(
            location="120.21,30.29",
            formatted="浙江省杭州市上城区杭州东站",
            adcode="330102",
        ),
        _geocode_item(
            location="120.18,30.26",
            formatted="浙江省杭州市上城区东站路某大厦",
            adcode="330102",
        ),
    ]
    try:
        resolve_geocode_point(items, city="杭州", address="东站", party_label="第一人")
        assert False, "should be ambiguous"
    except AppError as exc:
        assert exc.code == "GEOCODE_AMBIGUOUS"
        assert exc.status_code == 422


def test_collect_pois_drops_empty_address_and_does_not_fill_zero():
    mid_lon, mid_lat = 120.2, 30.3
    poi_lon, poi_lat = 120.21, 30.31
    payload = _around_ok(
        [
            _poi(name="空地址店", address=[], location=f"{poi_lon},{poi_lat}", distance="15"),
            _poi(
                name="缺距离店",
                address="上城区某路1号",
                location=f"{poi_lon},{poi_lat}",
                distance=[],
            ),
            _poi(name="无名店", address="上城区某路2号", location=f"{poi_lon},{poi_lat}", distance="9"),
        ]
    )
    pois = collect_pois(payload, midpoint_lon=mid_lon, midpoint_lat=mid_lat)
    assert len(pois) == 1
    assert pois[0].name == "缺距离店"
    assert pois[0].distance_to_midpoint_m != 0
    expected = distance_m(poi_lon, poi_lat, mid_lon, mid_lat)
    assert abs(pois[0].distance_to_midpoint_m - expected) < 1


def test_search_success_saves_created_at_and_coordinate_order(monkeypatch, tmp_path):
    monkeypatch.setattr("services.search_storage.SEARCH_DIR", tmp_path)
    around_locations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if path.endswith("/geocode/geo"):
            address = params.get("address") or ""
            if "东站" in address:
                return httpx.Response(
                    200,
                    json=_geocode_ok(
                        _geocode_item(
                            location=f"{EAST_LON},{EAST_LAT}",
                            formatted="浙江省杭州市上城区杭州东站",
                            level="公交地铁站点",
                        )
                    ),
                )
            return httpx.Response(
                200,
                json=_geocode_ok(
                    _geocode_item(
                        location=f"{BRIDGE_LON},{BRIDGE_LAT}",
                        formatted="浙江省杭州市西湖区龙翔桥地铁站",
                        level="公交地铁站点",
                        city="杭州市",
                        adcode="330106",
                    )
                ),
            )
        if path.endswith("/place/around"):
            around_locations.append(params.get("location") or "")
            assert params.get("radius") == "2000"
            lon_text, lat_text = around_locations[-1].split(",")
            assert float(lon_text) > float(lat_text)
            return httpx.Response(
                200,
                json=_around_ok(
                    [
                        _poi(
                            name="远店",
                            address="西湖区远路3号",
                            location="120.20,30.29",
                            distance="400",
                        ),
                        _poi(
                            name="近店",
                            address="西湖区近路1号",
                            location="120.186,30.275",
                            distance="80",
                        ),
                        _poi(
                            name="中店",
                            address="西湖区中路2号",
                            location="120.19,30.28",
                            distance="200",
                        ),
                        _poi(
                            name="第四家",
                            address="西湖区四路4号",
                            location="120.21,30.30",
                            distance="900",
                        ),
                    ]
                ),
            )
        return httpx.Response(404, json={"status": "0"})

    _install_amap(monkeypatch, handler)
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["search_id"].startswith("srch_")
    expected_lon, expected_lat = midpoint(EAST_LON, EAST_LAT, BRIDGE_LON, BRIDGE_LAT)
    assert abs(data["midpoint"]["longitude"] - expected_lon) < 1e-9
    assert abs(data["midpoint"]["latitude"] - expected_lat) < 1e-9
    names = [poi["name"] for poi in data["pois"]]
    assert names == ["近店", "中店", "远店"]
    distances = [poi["distance_to_midpoint_m"] for poi in data["pois"]]
    assert distances == sorted(distances)
    record = json.loads((tmp_path / f"{data['search_id']}.json").read_text(encoding="utf-8"))
    assert record["created_at"]
    assert record["radius_m"] == 2000
    sent_lon, sent_lat = around_locations[0].split(",")
    assert abs(float(sent_lon) - expected_lon) < 1e-6
    assert abs(float(sent_lat) - expected_lat) < 1e-6


def test_search_expands_radius_then_returns_pois(monkeypatch, tmp_path):
    monkeypatch.setattr("services.search_storage.SEARCH_DIR", tmp_path)
    radii: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if path.endswith("/geocode/geo"):
            address = params.get("address") or ""
            loc = f"{EAST_LON},{EAST_LAT}" if "东站" in address else f"{BRIDGE_LON},{BRIDGE_LAT}"
            name = "浙江省杭州市上城区杭州东站" if "东站" in address else "浙江省杭州市西湖区龙翔桥地铁站"
            return httpx.Response(
                200,
                json=_geocode_ok(_geocode_item(location=loc, formatted=name, level="公交地铁站点")),
            )
        radii.append(params.get("radius") or "")
        if params.get("radius") == "2000":
            return httpx.Response(200, json=_around_ok([]))
        return httpx.Response(
            200,
            json=_around_ok(
                [_poi(name="扩圈店", address="西湖区某路1号", location="120.19,30.28", distance="3200")]
            ),
        )

    _install_amap(monkeypatch, handler)
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 200
    assert radii == ["2000", "5000"]
    data = response.json()["data"]
    assert data["pois"][0]["name"] == "扩圈店"
    record = json.loads((tmp_path / f"{data['search_id']}.json").read_text(encoding="utf-8"))
    assert record["radius_m"] == 5000


def test_search_no_poi_returns_422(monkeypatch, tmp_path):
    monkeypatch.setattr("services.search_storage.SEARCH_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/geocode/geo"):
            address = request.url.params.get("address") or ""
            loc = f"{EAST_LON},{EAST_LAT}" if "东站" in address else f"{BRIDGE_LON},{BRIDGE_LAT}"
            name = "浙江省杭州市上城区杭州东站" if "东站" in address else "浙江省杭州市西湖区龙翔桥地铁站"
            return httpx.Response(
                200,
                json=_geocode_ok(_geocode_item(location=loc, formatted=name, level="公交地铁站点")),
            )
        return httpx.Response(200, json=_around_ok([]))

    _install_amap(monkeypatch, handler)
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "NO_POI"
    assert error["stage"] == "search"
    assert list(tmp_path.glob("srch_*.json")) == []


def test_search_unmatched_returns_422(monkeypatch, tmp_path):
    monkeypatch.setattr("services.search_storage.SEARCH_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/geocode/geo"):
            address = request.url.params.get("address") or ""
            if "西湖区" in address:
                return httpx.Response(
                    200,
                    json=_geocode_ok(
                        _geocode_item(
                            location="120.13,30.27",
                            formatted="浙江省杭州市西湖区",
                            level="区县",
                        )
                    ),
                )
            return httpx.Response(
                200,
                json=_geocode_ok(
                    _geocode_item(
                        location=f"{BRIDGE_LON},{BRIDGE_LAT}",
                        formatted="浙江省杭州市西湖区龙翔桥地铁站",
                        level="公交地铁站点",
                    )
                ),
            )
        return httpx.Response(200, json=_around_ok([]))

    _install_amap(monkeypatch, handler)
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "西湖区",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GEOCODE_UNMATCHED"


def test_search_missing_key_returns_502(monkeypatch):
    monkeypatch.setattr(search_service.settings, "amap_api_key", "")
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


def test_search_timeout_returns_504(monkeypatch):
    monkeypatch.setattr(search_service.settings, "amap_api_key", "test-key")

    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(search_service.httpx, "AsyncClient", TimeoutClient)
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_search_missing_field_returns_422():
    response = client.post("/search", json={"city_a": "杭州", "address_a": "杭州东站"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["stage"] == "search"


def test_search_cross_city_returns_422():
    response = client.post(
        "/search",
        json={
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "上海",
            "address_b": "人民广场",
            "category": "咖啡店",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXTRACT_CROSS_CITY"
