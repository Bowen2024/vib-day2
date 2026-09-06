from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from errors import AppError

AUDIO_DIR = Path(__file__).resolve().parent.parent / "storage" / "audio"
AUDIO_TTL = timedelta(hours=24)


def new_audio_id() -> str:
    return f"aud_{uuid4().hex[:16]}"


def save_upload(
    *,
    content: bytes,
    probe_container: str,
    probe_codec: str,
    duration_sec: float,
    original_name: str | None,
) -> str:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_id = new_audio_id()
    suffix = ".webm" if probe_container == "webm" else f".{probe_container}"
    audio_path = AUDIO_DIR / f"{audio_id}{suffix}"
    meta_path = AUDIO_DIR / f"{audio_id}.json"
    created_at = datetime.now(timezone.utc).isoformat()

    audio_path.write_bytes(content)
    meta_path.write_text(
        json.dumps(
            {
                "audio_id": audio_id,
                "created_at": created_at,
                "filename": f"{audio_id}{suffix}",
                "original_name": original_name or "",
                "container": probe_container,
                "codec": probe_codec,
                "duration_sec": duration_sec,
                "size_bytes": len(content),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return audio_id


def load_upload(audio_id: str, *, stage: str) -> tuple[bytes, dict]:
    if (
        not audio_id
        or "/" in audio_id
        or "\\" in audio_id
        or ".." in audio_id
        or not audio_id.startswith("aud_")
    ):
        raise AppError(
            404,
            "AUDIO_NOT_FOUND",
            "录音不存在或已过期，请重新录音。",
            stage,
        )

    meta_path = AUDIO_DIR / f"{audio_id}.json"
    if not meta_path.is_file():
        raise AppError(
            404,
            "AUDIO_NOT_FOUND",
            "录音不存在或已过期，请重新录音。",
            stage,
        )

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(meta["created_at"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AppError(
            404,
            "AUDIO_NOT_FOUND",
            "录音不存在或已过期，请重新录音。",
            stage,
        ) from exc

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at > AUDIO_TTL:
        raise AppError(
            404,
            "AUDIO_NOT_FOUND",
            "录音不存在或已过期，请重新录音。",
            stage,
        )

    filename = str(meta.get("filename") or "")
    audio_path = AUDIO_DIR / filename
    if not filename or audio_path.parent != AUDIO_DIR or not audio_path.is_file():
        raise AppError(
            404,
            "AUDIO_NOT_FOUND",
            "录音不存在或已过期，请重新录音。",
            stage,
        )
    return audio_path.read_bytes(), meta
