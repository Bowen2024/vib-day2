from __future__ import annotations

import base64
import logging
import time

import httpx

from config import settings
from errors import AppError

logger = logging.getLogger(__name__)

ASR_CONNECT_TIMEOUT_SEC = 5.0
ASR_TIMEOUT_SEC = 25.0
MAX_BASE64_BYTES = 10 * 1024 * 1024


def _mime_for_container(container: str) -> str:
    if container == "webm":
        return "audio/webm"
    return "audio/webm"


def _extract_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


async def recognize_audio(content: bytes, *, container: str, audio_id: str) -> str:
    if not settings.bailian_api_key:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "语音识别服务未配置密钥，无法调用。",
            "asr",
        )

    encoded = base64.b64encode(content).decode("ascii")
    if len(encoded.encode("utf-8")) > MAX_BASE64_BYTES:
        raise AppError(
            413,
            "AUDIO_TOO_LARGE",
            "编码后的录音超过识别服务限制，请缩短录音后再试。",
            "asr",
        )

    data_url = f"data:{_mime_for_container(container)};base64,{encoded}"
    body = {
        "model": settings.bailian_asr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_url},
                    }
                ],
            }
        ],
        "stream": False,
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(ASR_TIMEOUT_SEC, connect=ASR_CONNECT_TIMEOUT_SEC)
        ) as client:
            response = await client.post(
                settings.bailian_asr_url,
                headers={
                    "Authorization": f"Bearer {settings.bailian_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("asr timeout audio_id=%s elapsed_ms=%s", audio_id, elapsed_ms)
        raise AppError(
            504,
            "UPSTREAM_TIMEOUT",
            "语音识别超时，请稍后重试。",
            "asr",
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("asr http error audio_id=%s elapsed_ms=%s", audio_id, elapsed_ms)
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "语音识别服务异常，请稍后重试。",
            "asr",
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "asr upstream status=%s audio_id=%s elapsed_ms=%s",
        response.status_code,
        audio_id,
        elapsed_ms,
    )
    if response.status_code >= 400:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "语音识别服务异常，请稍后重试。",
            "asr",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(
            502,
            "UPSTREAM_ERROR",
            "语音识别服务异常，请稍后重试。",
            "asr",
        ) from exc

    text = _extract_text(payload)
    logger.info("asr done audio_id=%s text_len=%s elapsed_ms=%s", audio_id, len(text), elapsed_ms)
    if not text:
        raise AppError(
            422,
            "ASR_EMPTY",
            "没有听清内容，请重新说一次。",
            "asr",
        )
    return text
