from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from config import settings
from errors import AppError
from schemas import ExtractData, ExtractModelOutput

logger = logging.getLogger(__name__)

EXTRACT_CONNECT_TIMEOUT_SEC = 5.0
EXTRACT_TIMEOUT_SEC = 15.0
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract.txt"

HANGZHOU_DISTRICTS = {
    "上城",
    "拱墅",
    "西湖",
    "滨江",
    "萧山",
    "余杭",
    "临平",
    "钱塘",
    "富阳",
    "临安",
}
CATEGORY_ALIASES = {
    "喝咖啡": "咖啡店",
    "咖啡": "咖啡店",
    "咖啡馆": "咖啡店",
}
VAGUE_ADDRESSES = {"我家", "你家", "他家", "公司", "单位", "办公室", "家里"}


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_city(city: str | None, *, page_city: str) -> str | None:
    value = _blank_to_none(city) or _blank_to_none(page_city)
    if value is None:
        return None
    if value.endswith("市"):
        value = value[:-1]
    core = value[:-1] if value.endswith("区") else value
    if core in HANGZHOU_DISTRICTS or value in HANGZHOU_DISTRICTS:
        return "杭州"
    return value


def normalize_category(category: str | None) -> str:
    value = _blank_to_none(category)
    if value is None:
        return "咖啡店"
    return CATEGORY_ALIASES.get(value, value)


def is_vague_address(address: str | None) -> bool:
    value = _blank_to_none(address)
    if value is None:
        return True
    if value in VAGUE_ADDRESSES:
        return True
    return value.startswith("我家") and len(value) <= 4


def parse_model_output(raw: str) -> ExtractModelOutput:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(
            502,
            "MODEL_OUTPUT_INVALID",
            "地址提取结果格式异常，请稍后重试。",
            "extract",
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            502,
            "MODEL_OUTPUT_INVALID",
            "地址提取结果格式异常，请稍后重试。",
            "extract",
        )
    try:
        return ExtractModelOutput.model_validate(payload)
    except ValidationError as exc:
        raise AppError(
            502,
            "MODEL_OUTPUT_INVALID",
            "地址提取结果格式异常，请稍后重试。",
            "extract",
        ) from exc


def evaluate_business(model: ExtractModelOutput, *, page_city: str) -> ExtractData:
    if model.party_count != 2:
        raise AppError(
            422,
            "EXTRACT_PARTY_COUNT",
            "目前只支持两个人约碰面，请只说两个人的位置。",
            "extract",
        )

    address_a = None if is_vague_address(model.address_a) else _blank_to_none(model.address_a)
    address_b = None if is_vague_address(model.address_b) else _blank_to_none(model.address_b)
    if address_a is None or address_b is None:
        raise AppError(
            422,
            "EXTRACT_INCOMPLETE",
            "还缺少其中一个人的具体地点，请重新说清两个人分别在哪。",
            "extract",
        )

    city_a = normalize_city(model.city_a, page_city=page_city)
    city_b = normalize_city(model.city_b, page_city=page_city)
    if city_a is None or city_b is None:
        raise AppError(
            422,
            "EXTRACT_INCOMPLETE",
            "还缺少其中一个人的具体地点，请重新说清两个人分别在哪。",
            "extract",
        )
    if city_a != city_b:
        raise AppError(
            422,
            "EXTRACT_CROSS_CITY",
            "目前只支持同一座城市内查找，请重新说两个人所在的城市和地点。",
            "extract",
        )

    return ExtractData(
        city_a=city_a,
        address_a=address_a,
        city_b=city_b,
        address_b=address_b,
        category=normalize_category(model.category),
    )


def _message_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


async def call_deepseek(text: str, city: str) -> str:
    if not settings.deepseek_api_key:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "地址提取服务未配置密钥，无法调用。",
            "extract",
        )

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _load_prompt()},
            {
                "role": "user",
                "content": f"页面选定城市：{city}\n用户原话：{text}",
            },
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(EXTRACT_TIMEOUT_SEC, connect=EXTRACT_CONNECT_TIMEOUT_SEC)
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("extract timeout elapsed_ms=%s", elapsed_ms)
        raise AppError(
            504,
            "UPSTREAM_TIMEOUT",
            "地址提取超时，请稍后重试。",
            "extract",
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("extract http error elapsed_ms=%s", elapsed_ms)
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "地址提取服务异常，请稍后重试。",
            "extract",
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("extract upstream status=%s elapsed_ms=%s", response.status_code, elapsed_ms)
    if response.status_code >= 400:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "地址提取服务异常，请稍后重试。",
            "extract",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "MODEL_OUTPUT_INVALID",
            "地址提取结果格式异常，请稍后重试。",
            "extract",
        ) from exc

    raw = _message_content(payload)
    logger.info("extract done text_len=%s raw_len=%s elapsed_ms=%s", len(text), len(raw), elapsed_ms)
    if not raw:
        raise AppError(
            502,
            "MODEL_OUTPUT_INVALID",
            "地址提取结果格式异常，请稍后重试。",
            "extract",
        )
    return raw


async def extract_meeting(text: str, city: str) -> ExtractData:
    raw = await call_deepseek(text, city)
    model = parse_model_output(raw)
    return evaluate_business(model, page_city=city)
