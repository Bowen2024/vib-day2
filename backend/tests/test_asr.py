import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from errors import AppError
from main import app
from services import asr as asr_service
from services.audio_storage import save_upload


client = TestClient(app)


def _write_clip(tmp_path, *, created_at: str | None = None) -> str:
    audio_id = save_upload(
        content=b"fake-webm-bytes",
        probe_container="webm",
        probe_codec="opus",
        duration_sec=3.2,
        original_name="recording.webm",
    )
    if created_at is not None:
        meta_path = tmp_path / f"{audio_id}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["created_at"] = created_at
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return audio_id


def test_asr_success_returns_upstream_text(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    audio_id = _write_clip(tmp_path)

    async def fake_recognize(content, *, container, audio_id):
        assert content == b"fake-webm-bytes"
        assert container == "webm"
        return "我在杭州东站，朋友在西湖龙翔桥地铁站。"

    monkeypatch.setattr("api.asr.recognize_audio", fake_recognize)

    response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["text"] == "我在杭州东站，朋友在西湖龙翔桥地铁站。"
    assert body["request_id"]


def test_asr_unknown_id_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    response = client.post("/asr", json={"audio_id": "aud_notexist123456"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "AUDIO_NOT_FOUND"
    assert error["stage"] == "asr"


def test_asr_expired_id_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    expired = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    audio_id = _write_clip(tmp_path, created_at=expired)

    response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AUDIO_NOT_FOUND"


def test_asr_empty_text_returns_422(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    audio_id = _write_clip(tmp_path)

    async def fake_empty(content, *, container, audio_id):
        raise AppError(422, "ASR_EMPTY", "没有听清内容，请重新说一次。", "asr")

    monkeypatch.setattr("api.asr.recognize_audio", fake_empty)
    response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "ASR_EMPTY"
    assert error["stage"] == "asr"


def test_asr_timeout_returns_504(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    audio_id = _write_clip(tmp_path)

    async def fake_timeout(content, *, container, audio_id):
        raise AppError(504, "UPSTREAM_TIMEOUT", "语音识别超时，请稍后重试。", "asr")

    monkeypatch.setattr("api.asr.recognize_audio", fake_timeout)
    response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_asr_missing_field_returns_422():
    response = client.post("/asr", json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["stage"] == "asr"


def test_recognize_audio_uses_upstream_payload(monkeypatch):
    monkeypatch.setattr(asr_service.settings, "bailian_api_key", "test-key")
    monkeypatch.setattr(
        asr_service.settings,
        "bailian_asr_url",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "sk-" not in request.content.decode("utf-8")
        assert "test-key" not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " 识别出的用户原话 "}}]},
        )

    transport = httpx.MockTransport(handler)

    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(asr_service.httpx, "AsyncClient", fake_client)

    import asyncio

    text = asyncio.run(asr_service.recognize_audio(b"abc", container="webm", audio_id="aud_test"))
    assert text == "识别出的用户原话"
