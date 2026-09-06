from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile

from errors import AppError
from schemas import UploadData, UploadResponse
from services.audio_probe import probe_audio
from services.audio_storage import save_upload

router = APIRouter()

MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_DURATION_SEC = 1.0
MAX_DURATION_SEC = 60.0


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


@router.post("/upload", response_model=UploadResponse)
async def upload(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_FILE_BYTES + 4096:
                raise AppError(
                    413,
                    "AUDIO_TOO_LARGE",
                    "录音文件超过 5MB，请缩短录音后再试。",
                    "upload",
                )
        except ValueError:
            pass

    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise AppError(
            413,
            "AUDIO_TOO_LARGE",
            "录音文件超过 5MB，请缩短录音后再试。",
            "upload",
        )
    if not content:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请上传录音文件。",
            "upload",
        )

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        probe = probe_audio(tmp_path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    if probe.duration_sec < MIN_DURATION_SEC or probe.duration_sec > MAX_DURATION_SEC:
        raise AppError(
            422,
            "AUDIO_DURATION_INVALID",
            "录音需在 1 到 60 秒之间，请重新录制。",
            "upload",
        )

    audio_id = save_upload(
        content=content,
        probe_container=probe.container,
        probe_codec=probe.codec,
        duration_sec=probe.duration_sec,
        original_name=file.filename,
    )
    return UploadResponse(request_id=_request_id(request), data=UploadData(audio_id=audio_id))
