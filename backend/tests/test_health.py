from fastapi.testclient import TestClient

from main import app


def test_health_returns_ok_without_external_keys():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("request_id"), str) and body["request_id"]
    assert body["data"] == {"status": "ok"}
