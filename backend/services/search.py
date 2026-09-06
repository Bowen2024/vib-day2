from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass

import httpx

from config import settings
from errors import AppError
from schemas import Midpoint, SearchData, SearchPoi, SearchRequest
from services.extract import HANGZHOU_DISTRICTS, normalize_category, normalize_city
from services.search_storage import save_search

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SEC = 5.0
GEOCODE_TIMEOUT_SEC = 8.0
AROUND_TIMEOUT_SEC = 8.0
SEARCH_TOTAL_BUDGET_SEC = 28.0

RADIUS_FIRST_M = 2000
RADIUS_EXPAND_M = 5000
AROUND_OFFSET = 20
MAX_POIS = 3
SAME_PLACE_MAX_M = 250.0
EARTH_RADIUS_M = 6_371_000.0

COARSE_LEVELS = {"国家", "省", "市", "区县", "未知"}
ACCEPTED_LEVELS = {
    "兴趣点",
    "公交地铁站点",
    "门牌号",
    "门址",
    "道路交叉路口",
    "热点商圈",
    "住宅区",
    "单元号",
    "楼层",
    "房间",
}
LEVEL_RANK = {
    "房间": 0,
    "楼层": 1,
    "单元号": 2,
    "门址": 3,
    "门牌号": 4,
    "公交地铁站点": 5,
    "兴趣点": 6,
    "道路交叉路口": 7,
    "住宅区": 8,
    "热点商圈": 9,
}
NAME_SUFFIXES = (
    "出入口",
    "地铁站",
    "高铁站",
    "火车站",
    "汽车站",
    "西北口",
    "东北口",
    "西南口",
    "东南口",
    "出口",
    "入口",
    "站",
)


@dataclass(frozen=True)
class GeoPoint:
    longitude: float
    latitude: float
    name: str
    address: str
    adcode: str
    level: str


def amap_text(value: object) -> str | None:
    if value is None or isinstance(value, list):
        return None
    text = str(value).strip()
    return text or None


def parse_location(value: object) -> tuple[float, float] | None:
    text = amap_text(value)
    if text is None or "," not in text:
        return None
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        longitude = float(parts[0].strip())
        latitude = float(parts[1].strip())
    except ValueError:
        return None
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        return None
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        return None
    return longitude, latitude


def distance_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    phi1 = math.radians(lat_a)
    phi2 = math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    hav = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(hav)))


def midpoint(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> tuple[float, float]:
    return (lon_a + lon_b) / 2.0, (lat_a + lat_b) / 2.0


def format_location(longitude: float, latitude: float) -> str:
    return f"{longitude:.6f},{latitude:.6f}"


def core_name(name: str) -> str:
    value = name.strip()
    original = value
    changed = True
    while value and changed:
        changed = False
        for suffix in NAME_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)]
                changed = True
                break
        else:
            if re.search(r"[A-Za-z]口$", value) and len(value) > 2:
                value = value[:-2]
                changed = True
    value = value.strip()
    if len(value) >= 4:
        return value
    return original if len(original) >= 4 else value


def _query_tokens(address: str, city: str) -> list[str]:
    tokens: list[str] = []
    for candidate in (address.strip(), core_name(address.strip())):
        if len(candidate) >= 2 and candidate not in tokens:
            tokens.append(candidate)

    strip_parts = [city + "市", city + "区", city, *HANGZHOU_DISTRICTS]
    expected = normalize_city(city, page_city=city)
    if expected:
        strip_parts.extend([expected + "市", expected + "区", expected])
    text = address
    for piece in sorted(set(strip_parts), key=len, reverse=True):
        if piece:
            text = text.replace(piece, "")
    text = text.strip()
    if len(text) >= 2 and text not in tokens:
        tokens.append(text)
    core = core_name(text) if text else ""
    if len(core) >= 2 and core not in tokens:
        tokens.append(core)
    for part in re.split(r"[\s,，、/]+", text):
        part = part.strip()
        if len(part) >= 2 and part not in tokens:
            tokens.append(part)
    return tokens


def _haystack(item: dict) -> str:
    parts = [
        amap_text(item.get("formatted_address")),
        amap_text(item.get("district")),
        amap_text(item.get("street")),
        amap_text(item.get("number")),
        amap_text(item.get("township")),
    ]
    return " ".join(part for part in parts if part)


def _city_matches(item: dict, request_city: str) -> bool:
    expected = normalize_city(request_city, page_city=request_city)
    if expected is None:
        return False
    raw_city = amap_text(item.get("city")) or amap_text(item.get("province"))
    if raw_city:
        actual = normalize_city(raw_city, page_city=request_city)
        return actual == expected
    formatted = amap_text(item.get("formatted_address")) or ""
    return expected in formatted or f"{expected}市" in formatted


def _display_name(item: dict, fallback: str) -> str:
    return (
        amap_text(item.get("formatted_address"))
        or "".join(
            part
            for part in (
                amap_text(item.get("district")),
                amap_text(item.get("street")),
                amap_text(item.get("number")),
            )
            if part
        )
        or fallback
    )


def _as_geocodes(payload: dict) -> list[dict]:
    geocodes = payload.get("geocodes")
    if geocodes is None:
        return []
    if isinstance(geocodes, dict):
        return [geocodes]
    if isinstance(geocodes, list):
        return [item for item in geocodes if isinstance(item, dict)]
    return []


def same_place(left: GeoPoint, right: GeoPoint) -> bool:
    if not left.adcode or left.adcode != right.adcode:
        return False
    left_core = core_name(left.name)
    right_core = core_name(right.name)
    if len(left_core) < 4 or len(right_core) < 4:
        return False
    if left_core != right_core and left_core not in right_core and right_core not in left_core:
        return False
    return (
        distance_m(left.longitude, left.latitude, right.longitude, right.latitude)
        <= SAME_PLACE_MAX_M
    )


def _cluster(points: list[GeoPoint]) -> list[list[GeoPoint]]:
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, left in enumerate(points):
        for j, right in enumerate(points[i + 1 :], start=i + 1):
            if same_place(left, right):
                parent[find(j)] = find(i)

    groups: dict[int, list[GeoPoint]] = {}
    for index, point in enumerate(points):
        groups.setdefault(find(index), []).append(point)
    return list(groups.values())


def _pick_cluster(cluster: list[GeoPoint]) -> GeoPoint:
    return min(
        cluster,
        key=lambda point: (LEVEL_RANK.get(point.level, 50), len(point.name), point.name),
    )


def filter_geocode_candidates(
    geocodes: list[dict],
    *,
    city: str,
    address: str,
) -> list[GeoPoint]:
    kept: list[GeoPoint] = []
    for item in geocodes:
        level = amap_text(item.get("level")) or "未知"
        if level in COARSE_LEVELS or level not in ACCEPTED_LEVELS:
            continue
        location = parse_location(item.get("location"))
        if location is None:
            continue
        if not _city_matches(item, city):
            continue
        if not any(token in _haystack(item) for token in _query_tokens(address, city)):
            continue
        adcode = amap_text(item.get("adcode")) or ""
        name = _display_name(item, address)
        kept.append(
            GeoPoint(
                longitude=location[0],
                latitude=location[1],
                name=name,
                address=amap_text(item.get("formatted_address")) or address,
                adcode=adcode,
                level=level,
            )
        )
    return kept


def resolve_geocode_point(
    geocodes: list[dict],
    *,
    city: str,
    address: str,
    party_label: str,
) -> GeoPoint:
    remaining = filter_geocode_candidates(geocodes, city=city, address=address)
    if not remaining:
        raise AppError(
            422,
            "GEOCODE_UNMATCHED",
            f"无法确认{party_label}的具体地点，请重新说清两个人分别在哪。",
            "search",
        )
    clusters = _cluster(remaining)
    if len(clusters) >= 2:
        names: list[str] = []
        for cluster in clusters:
            label = _pick_cluster(cluster).name
            if label not in names:
                names.append(label)
            if len(names) == 3:
                break
        listed = "、".join(names)
        raise AppError(
            422,
            "GEOCODE_AMBIGUOUS",
            f"{party_label}的地点不够明确，可能是：{listed}。请补充更具体的地点。",
            "search",
        )
    return _pick_cluster(clusters[0])


def _remaining_timeout(deadline: float, per_call: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise AppError(
            504,
            "UPSTREAM_TIMEOUT",
            "搜店超时，请稍后重试。",
            "search",
        )
    return min(per_call, left)


def _amap_ok(payload: dict) -> bool:
    status = payload.get("status")
    return status == 1 or status == "1"


async def _amap_get(url: str, params: dict, *, timeout_sec: float) -> dict:
    if not settings.amap_api_key:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "搜店服务未配置密钥，无法调用。",
            "search",
        )
    query = {**params, "key": settings.amap_api_key, "output": "JSON"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec, connect=min(CONNECT_TIMEOUT_SEC, timeout_sec))
        ) as client:
            response = await client.get(url, params=query)
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("search amap timeout elapsed_ms=%s", elapsed_ms)
        raise AppError(
            504,
            "UPSTREAM_TIMEOUT",
            "搜店超时，请稍后重试。",
            "search",
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("search amap http error elapsed_ms=%s", elapsed_ms)
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "搜店服务异常，请稍后重试。",
            "search",
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("search amap status=%s elapsed_ms=%s", response.status_code, elapsed_ms)
    if response.status_code >= 400:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "搜店服务异常，请稍后重试。",
            "search",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "搜店服务异常，请稍后重试。",
            "search",
        ) from exc
    if not isinstance(payload, dict) or not _amap_ok(payload):
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "搜店服务异常，请稍后重试。",
            "search",
        )
    return payload


async def geocode_address(city: str, address: str, *, timeout_sec: float) -> dict:
    return await _amap_get(
        settings.amap_geocode_url,
        {"address": address, "city": city},
        timeout_sec=timeout_sec,
    )


def _parse_distance(raw: object) -> float | None:
    text = amap_text(raw)
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def collect_pois(
    payload: dict,
    *,
    midpoint_lon: float,
    midpoint_lat: float,
) -> list[SearchPoi]:
    pois = payload.get("pois") or []
    if isinstance(pois, dict):
        pois = [pois]
    collected: list[SearchPoi] = []
    for item in pois:
        if not isinstance(item, dict):
            continue
        name = amap_text(item.get("name"))
        address = amap_text(item.get("address"))
        location = parse_location(item.get("location"))
        if not name or not address or location is None:
            continue
        distance = _parse_distance(item.get("distance"))
        if distance is None:
            distance = distance_m(location[0], location[1], midpoint_lon, midpoint_lat)
        collected.append(
            SearchPoi(name=name, address=address, distance_to_midpoint_m=distance)
        )
    collected.sort(key=lambda poi: (poi.distance_to_midpoint_m, poi.name))
    return collected[:MAX_POIS]


async def around_search(
    *,
    longitude: float,
    latitude: float,
    keywords: str,
    city: str,
    radius: int,
    timeout_sec: float,
) -> dict:
    return await _amap_get(
        settings.amap_around_url,
        {
            "location": format_location(longitude, latitude),
            "keywords": keywords,
            "city": city,
            "citylimit": "true",
            "radius": str(radius),
            "offset": str(AROUND_OFFSET),
            "page": "1",
            "sortrule": "distance",
        },
        timeout_sec=timeout_sec,
    )


def _require_same_city(city_a: str, city_b: str) -> str:
    left = normalize_city(city_a, page_city=city_a)
    right = normalize_city(city_b, page_city=city_b)
    if left is None or right is None or left != right:
        raise AppError(
            422,
            "EXTRACT_CROSS_CITY",
            "目前只支持同一座城市内查找，请重新说两个人所在的城市和地点。",
            "search",
        )
    return left


async def search_meeting(payload: SearchRequest) -> SearchData:
    city_a = payload.city_a.strip()
    city_b = payload.city_b.strip()
    address_a = payload.address_a.strip()
    address_b = payload.address_b.strip()
    category = normalize_category(payload.category.strip())
    if not city_a or not city_b or not address_a or not address_b or not category:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求缺少文件或字段类型不正确。",
            "search",
        )
    city = _require_same_city(city_a, city_b)
    deadline = time.monotonic() + SEARCH_TOTAL_BUDGET_SEC

    geocode_a_raw, geocode_b_raw = await asyncio.gather(
        geocode_address(city, address_a, timeout_sec=_remaining_timeout(deadline, GEOCODE_TIMEOUT_SEC)),
        geocode_address(city, address_b, timeout_sec=_remaining_timeout(deadline, GEOCODE_TIMEOUT_SEC)),
    )
    point_a = resolve_geocode_point(
        _as_geocodes(geocode_a_raw),
        city=city,
        address=address_a,
        party_label="第一人",
    )
    point_b = resolve_geocode_point(
        _as_geocodes(geocode_b_raw),
        city=city,
        address=address_b,
        party_label="第二人",
    )
    mid_lon, mid_lat = midpoint(point_a.longitude, point_a.latitude, point_b.longitude, point_b.latitude)

    around_first = await around_search(
        longitude=mid_lon,
        latitude=mid_lat,
        keywords=category,
        city=city,
        radius=RADIUS_FIRST_M,
        timeout_sec=_remaining_timeout(deadline, AROUND_TIMEOUT_SEC),
    )
    pois = collect_pois(around_first, midpoint_lon=mid_lon, midpoint_lat=mid_lat)
    used_radius = RADIUS_FIRST_M
    if not pois:
        around_expand = await around_search(
            longitude=mid_lon,
            latitude=mid_lat,
            keywords=category,
            city=city,
            radius=RADIUS_EXPAND_M,
            timeout_sec=_remaining_timeout(deadline, AROUND_TIMEOUT_SEC),
        )
        pois = collect_pois(around_expand, midpoint_lon=mid_lon, midpoint_lat=mid_lat)
        used_radius = RADIUS_EXPAND_M
    if not pois:
        raise AppError(
            422,
            "NO_POI",
            "中点附近暂时找不到合适的店，请换个类别或更具体的地点再试。",
            "search",
        )

    search_id = save_search(
        {
            "city": city,
            "category": category,
            "city_a": city,
            "address_a": address_a,
            "city_b": city,
            "address_b": address_b,
            "point_a": {
                "longitude": point_a.longitude,
                "latitude": point_a.latitude,
                "name": point_a.name,
            },
            "point_b": {
                "longitude": point_b.longitude,
                "latitude": point_b.latitude,
                "name": point_b.name,
            },
            "midpoint": {"longitude": mid_lon, "latitude": mid_lat},
            "radius_m": used_radius,
            "pois": [poi.model_dump() for poi in pois],
        }
    )
    logger.info(
        "search done search_id=%s radius_m=%s poi_count=%s",
        search_id,
        used_radius,
        len(pois),
    )
    return SearchData(
        search_id=search_id,
        midpoint=Midpoint(longitude=mid_lon, latitude=mid_lat),
        pois=pois,
    )
