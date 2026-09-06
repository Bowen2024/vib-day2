from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Request

from errors import AppError
from schemas import ExtractRequest, ExtractResponse
from services.extract import extract_meeting

router = APIRouter()

EXTRACT_EXAMPLES = {
    "complete": {
        "summary": "正常提取：两人同城、地址完整",
        "value": {
            "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店。",
            "city": "杭州",
        },
    },
    "spoken_city_first": {
        "summary": "口述城市优先：页面是杭州，口述是上海",
        "value": {
            "text": "我在上海人民广场，朋友在静安寺，帮我们找个咖啡店。",
            "city": "杭州",
        },
    },
    "page_city_fallback": {
        "summary": "页面默认城市：口述没提城市",
        "value": {
            "text": "我在东站，朋友在龙翔桥地铁站，找个咖啡店。",
            "city": "杭州",
        },
    },
    "category_alias": {
        "summary": "类别归一化：喝咖啡 → 咖啡店",
        "value": {
            "text": "我在杭州东站，朋友在西湖龙翔桥，帮我们找个地方喝咖啡。",
            "city": "杭州",
        },
    },
    "missing_address": {
        "summary": "地址缺失：只说了一个人",
        "value": {
            "text": "我在杭州东站，帮我们找个咖啡店。",
            "city": "杭州",
        },
    },
    "vague_home": {
        "summary": "含糊表达：我家",
        "value": {
            "text": "我在杭州东站，朋友在我家，帮我们找个咖啡店。",
            "city": "杭州",
        },
    },
    "party_count": {
        "summary": "人数不符：三个人",
        "value": {
            "text": "我、小王和小李三个人碰面，我在杭州东站，小王在龙翔桥。",
            "city": "杭州",
        },
    },
    "cross_city": {
        "summary": "跨城：杭州和上海",
        "value": {
            "text": "我在杭州东站，朋友在上海人民广场，帮我们找个咖啡店。",
            "city": "杭州",
        },
    },
    "hangzhou_district": {
        "summary": "区县折到地级市：余杭属于杭州",
        "value": {
            "text": "我在余杭西站，朋友在龙翔桥，帮我们找个咖啡店。",
            "city": "杭州",
        },
    },
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    request: Request,
    payload: Annotated[ExtractRequest, Body(openapi_examples=EXTRACT_EXAMPLES)],
) -> ExtractResponse:
    text = payload.text.strip()
    city = payload.city.strip() or "杭州"
    if not text:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求缺少文件或字段类型不正确。",
            "extract",
        )
    data = await extract_meeting(text, city)
    return ExtractResponse(request_id=_request_id(request), data=data)
