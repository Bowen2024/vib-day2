from io import BytesIO

from fastapi.testclient import TestClient

from errors import AppError
from main import app
from services.audio_probe import ProbeResult


client = TestClient(app)


def _ok_probe(_path) -> ProbeResult:
    return ProbeResult(container="webm", codec="opus", duration_sec=3.2)


def test_upload_success_returns_audio_id(monkeypatch, tmp_path):
    monkeypatch.setattr("api.upload.probe_audio", _ok_probe)
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)

    response = client.post(
        "/upload",
        files={"file": ("recording.webm", BytesIO(b"fake-webm-bytes"), "audio/webm")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["audio_id"].startswith("aud_")
    audio_id = body["data"]["audio_id"]
    assert (tmp_path / f"{audio_id}.webm").is_file()
    assert (tmp_path / f"{audio_id}.json").is_file()


def test_upload_too_large_returns_413(monkeypatch, tmp_path):
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)
    payload = b"x" * (5 * 1024 * 1024 + 1)

    response = client.post(
        "/upload",
        files={"file": ("huge.webm", BytesIO(payload), "audio/webm")},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "AUDIO_TOO_LARGE"
    assert error["stage"] == "upload"


def test_upload_unsupported_format_returns_415(monkeypatch, tmp_path):
    def boom(_path):
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        )

    monkeypatch.setattr("api.upload.probe_audio", boom)
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)

    response = client.post(
        "/upload",
        files={"file": ("clip.wav", BytesIO(b"RIFF"), "audio/wav")},
    )

    assert response.status_code == 415
    error = response.json()["error"]
    assert error["code"] == "AUDIO_UNSUPPORTED"
    assert error["stage"] == "upload"


def test_upload_duration_too_short_returns_422(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "api.upload.probe_audio",
        lambda _path: ProbeResult(container="webm", codec="opus", duration_sec=0.4),
    )
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)

    response = client.post(
        "/upload",
        files={"file": ("short.webm", BytesIO(b"fake-webm-bytes"), "audio/webm")},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "AUDIO_DURATION_INVALID"
    assert error["stage"] == "upload"


def test_upload_duration_too_long_returns_422(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "api.upload.probe_audio",
        lambda _path: ProbeResult(container="webm", codec="opus", duration_sec=61),
    )
    monkeypatch.setattr("services.audio_storage.AUDIO_DIR", tmp_path)

    response = client.post(
        "/upload",
        files={"file": ("long.webm", BytesIO(b"fake-webm-bytes"), "audio/webm")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AUDIO_DURATION_INVALID"


def test_upload_missing_file_returns_422():
    response = client.post("/upload")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "request_id" in response.json()
