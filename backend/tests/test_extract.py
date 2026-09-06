import json

import pytest
from fastapi.testclient import TestClient

from errors import AppError
from main import app
from schemas import ExtractModelOutput
from services.extract import evaluate_business, parse_model_output


client = TestClient(app)


def _model_json(**overrides) -> str:
    payload = {
        "city_a": "杭州",
        "address_a": "杭州东站",
        "city_b": "杭州",
        "address_b": "西湖龙翔桥地铁站",
        "category": "咖啡店",
        "party_count": 2,
        "incomplete_reason": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_extract_success_returns_five_fields(monkeypatch):
    async def fake_call(text, city):
        assert "杭州东站" in text
        return _model_json()

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)

    response = client.post(
        "/extract",
        json={
            "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店。",
            "city": "杭州",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "city_a": "杭州",
        "address_a": "杭州东站",
        "city_b": "杭州",
        "address_b": "西湖龙翔桥地铁站",
        "category": "咖啡店",
    }
    assert "party_count" not in response.json()["data"]


def test_extract_missing_address_returns_422(monkeypatch):
    async def fake_call(text, city):
        return _model_json(address_b=None, incomplete_reason="未说明第二人的具体地点")

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)
    response = client.post(
        "/extract",
        json={"text": "我在杭州东站，帮我们找个咖啡店。", "city": "杭州"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "EXTRACT_INCOMPLETE"
    assert error["stage"] == "extract"


def test_extract_vague_home_returns_422(monkeypatch):
    async def fake_call(text, city):
        return _model_json(address_b="我家")

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)
    response = client.post(
        "/extract",
        json={"text": "我在杭州东站，朋友在我家，找个咖啡店。", "city": "杭州"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXTRACT_INCOMPLETE"


def test_extract_party_count_returns_422(monkeypatch):
    async def fake_call(text, city):
        return _model_json(
            city_b=None,
            address_b=None,
            party_count=3,
            incomplete_reason="出现三人或以上",
        )

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)
    response = client.post(
        "/extract",
        json={"text": "我、小王和小李三个人，我在杭州东站。", "city": "杭州"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXTRACT_PARTY_COUNT"


def test_extract_cross_city_returns_422(monkeypatch):
    async def fake_call(text, city):
        return _model_json(city_b="上海", address_b="人民广场")

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)
    response = client.post(
        "/extract",
        json={"text": "我在杭州东站，朋友在上海人民广场，找咖啡店。", "city": "杭州"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXTRACT_CROSS_CITY"


def test_extract_invalid_json_returns_502(monkeypatch):
    async def fake_call(text, city):
        return "不是合法JSON"

    monkeypatch.setattr("services.extract.call_deepseek", fake_call)
    response = client.post(
        "/extract",
        json={"text": "我在杭州东站，朋友在龙翔桥。", "city": "杭州"},
    )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "MODEL_OUTPUT_INVALID"
    assert "没说清楚" not in error["message"]


def test_extract_wrong_field_type_returns_502():
    with pytest.raises(AppError) as exc_info:
        parse_model_output(_model_json(party_count="两人"))
    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"
    assert "没说清楚" not in exc_info.value.message


def test_extract_missing_model_field_returns_502():
    with pytest.raises(AppError) as exc_info:
        parse_model_output(json.dumps({"city_a": "杭州", "address_a": "杭州东站"}))
    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_extract_missing_key_returns_502(monkeypatch):
    monkeypatch.setattr("services.extract.settings.deepseek_api_key", "")
    response = client.post(
        "/extract",
        json={"text": "我在杭州东站，朋友在龙翔桥。", "city": "杭州"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


def test_extract_category_and_district_normalization():
    data = evaluate_business(
        ExtractModelOutput(
            city_a="余杭",
            address_a="余杭西站",
            city_b="杭州",
            address_b="龙翔桥",
            category="喝咖啡",
            party_count=2,
            incomplete_reason=None,
        ),
        page_city="杭州",
    )
    assert data.city_a == "杭州"
    assert data.city_b == "杭州"
    assert data.category == "咖啡店"


def test_extract_page_city_fills_null_city():
    data = evaluate_business(
        ExtractModelOutput(
            city_a=None,
            address_a="杭州东站",
            city_b=None,
            address_b="龙翔桥",
            category=None,
            party_count=2,
            incomplete_reason=None,
        ),
        page_city="杭州",
    )
    assert data.city_a == "杭州"
    assert data.city_b == "杭州"
    assert data.category == "咖啡店"


def test_extract_spoken_city_overrides_page_city():
    data = evaluate_business(
        ExtractModelOutput(
            city_a="上海",
            address_a="人民广场",
            city_b="上海",
            address_b="静安寺",
            category="咖啡馆",
            party_count=2,
            incomplete_reason=None,
        ),
        page_city="杭州",
    )
    assert data.city_a == "上海"
    assert data.city_b == "上海"
    assert data.category == "咖啡店"


def test_extract_missing_text_returns_422():
    response = client.post("/extract", json={"city": "杭州"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["stage"] == "extract"
