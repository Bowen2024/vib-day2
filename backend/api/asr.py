from uuid import uuid4

from fastapi import APIRouter, Request

from errors import AppError
from schemas import AsrData, AsrRequest, AsrResponse
from services.asr import recognize_audio
from services.audio_storage import load_upload

router = APIRouter()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


@router.post("/asr", response_model=AsrResponse)
async def asr(request: Request, payload: AsrRequest) -> AsrResponse:
    audio_id = payload.audio_id.strip()
    if not audio_id:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求缺少文件或字段类型不正确。",
            "asr",
        )

    content, meta = load_upload(audio_id, stage="asr")
    text = await recognize_audio(
        content,
        container=str(meta.get("container") or "webm"),
        audio_id=audio_id,
    )
    return AsrResponse(request_id=_request_id(request), data=AsrData(text=text))
