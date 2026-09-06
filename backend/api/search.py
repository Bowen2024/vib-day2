from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Request

from errors import AppError
from schemas import SearchRequest, SearchResponse
from services.search import search_meeting

router = APIRouter()

SEARCH_EXAMPLES = {
    "complete": {
        "summary": "正常搜店：杭州东站与龙翔桥，咖啡店",
        "value": {
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    },
    "unmatched": {
        "summary": "定位过粗：区县级，无法确认具体点",
        "value": {
            "city_a": "杭州",
            "address_a": "西湖区",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    },
    "ambiguous_road": {
        "summary": "定位不明确：道路名不是碰面点",
        "value": {
            "city_a": "杭州",
            "address_a": "延安路",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    },
    "no_poi_keyword": {
        "summary": "无候选：用罕见类别（真实高德仍可能搜到店）",
        "value": {
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "不存在的店类xyz",
        },
    },
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


@router.post("/search", response_model=SearchResponse)
async def search(
    request: Request,
    payload: Annotated[SearchRequest, Body(openapi_examples=SEARCH_EXAMPLES)],
) -> SearchResponse:
    if (
        not payload.city_a.strip()
        or not payload.city_b.strip()
        or not payload.address_a.strip()
        or not payload.address_b.strip()
        or not payload.category.strip()
    ):
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求缺少文件或字段类型不正确。",
            "search",
        )
    data = await search_meeting(payload)
    return SearchResponse(request_id=_request_id(request), data=data)
