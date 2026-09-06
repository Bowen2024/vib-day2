from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from errors import AppError

SEARCH_DIR = Path(__file__).resolve().parent.parent / "storage" / "searches"
SEARCH_TTL = timedelta(hours=24)


def new_search_id() -> str:
    return f"srch_{uuid4().hex[:16]}"


def save_search(payload: dict) -> str:
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    search_id = new_search_id()
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        **payload,
        "search_id": search_id,
        "created_at": created_at,
    }
    path = SEARCH_DIR / f"{search_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return search_id


def load_search(search_id: str, *, stage: str) -> dict:
    if (
        not search_id
        or "/" in search_id
        or "\\" in search_id
        or ".." in search_id
        or not search_id.startswith("srch_")
    ):
        raise AppError(
            404,
            "SEARCH_NOT_FOUND",
            "查询结果不存在或已过期，请重新查找。",
            stage,
        )

    path = SEARCH_DIR / f"{search_id}.json"
    if not path.is_file():
        raise AppError(
            404,
            "SEARCH_NOT_FOUND",
            "查询结果不存在或已过期，请重新查找。",
            stage,
        )

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(record["created_at"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AppError(
            404,
            "SEARCH_NOT_FOUND",
            "查询结果不存在或已过期，请重新查找。",
            stage,
        ) from exc

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at > SEARCH_TTL:
        raise AppError(
            404,
            "SEARCH_NOT_FOUND",
            "查询结果不存在或已过期，请重新查找。",
            stage,
        )
    return record
