import json
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import ClientError
from google.genai.models import AsyncModels
from google.genai.types import GenerateContentResponse
from pydantic import SecretStr

from app.core.config import settings
from app.prompts.system import SYSTEM_PROMPTS
from app.services.llm import get_llm


def gemini_response(analysis):
    content = {"role": "model", "parts": [{"text": json.dumps(analysis)}]}
    return GenerateContentResponse.model_validate({
        "candidates": [{"content": content}],
    })


@pytest.fixture(autouse=True)
def gemini(monkeypatch):
    generate = AsyncMock(return_value=gemini_response({
        "requirements": [{"id": "FR-01", "text": "Загрузка документов"}],
        "issues": [{"severity": "medium", "text": "Не заданы форматы файлов"}],
        "acceptance_criteria": ["Загруженный документ появляется в списке"],
    }))
    monkeypatch.setattr(settings, "google_api_key", SecretStr("test-gemini-key"))
    monkeypatch.setattr(AsyncModels, "generate_content", generate)
    get_llm.cache_clear()
    yield generate
    get_llm.cache_clear()


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "llm"}


def test_create_and_reload_history(client):
    response = client.post(
        "/api/messages",
        json={"text": "  Нужна загрузка документов  ", "mode": "analyst"},
    )
    assert response.status_code == 201
    turn = response.json()
    assert turn["user"]["content"] == "Нужна загрузка документов"
    assert turn["assistant"]["analysis"]["is_demo"] is False
    assert turn["assistant"]["analysis"]["requirements"][0]["id"] == "FR-01"

    history = client.get("/api/messages").json()
    assert [msg["role"] for msg in history] == ["user", "assistant"]
    assert history[1]["analysis"] == turn["assistant"]["analysis"]


@pytest.mark.parametrize("mode,section", [
    ("analyst", "requirements"),
    ("critic", "issues"),
    ("qa", "acceptance_criteria"),
])
def test_modes(client, gemini, mode, section):
    text = "Проверь требования {к документам}"
    response = client.post("/api/messages", json={"text": text, "mode": mode})
    assert response.status_code == 201
    assert response.json()["assistant"]["analysis"][section]

    gemini.assert_awaited_once()
    request = gemini.await_args.kwargs
    config = request["config"]
    assert config.system_instruction.parts[0].text == SYSTEM_PROMPTS[mode]
    message, = request["contents"]
    assert (message.role, message.parts[0].text) == ("user", text)
    assert config.response_mime_type == "application/json"
    assert "is_demo" not in config.response_json_schema["properties"]


def test_invalid_messages_are_rejected(client, gemini):
    for text in ["   ", "", "x" * 501]:
        assert client.post("/api/messages", json={"text": text}).status_code == 422
    assert client.post("/api/messages", json={"text": "ok", "mode": "other"}).status_code == 422
    gemini.assert_not_awaited()
    assert client.get("/api/messages").json() == []


def test_clear_history(client):
    assert client.post("/api/messages", json={"text": "Hello"}).status_code == 201
    assert client.delete("/api/messages").status_code == 204
    assert client.get("/api/messages").json() == []


@pytest.mark.parametrize("error", [
    TimeoutError("internal timeout"),
    ClientError(429, {"error": {"message": "internal quota error", "status": "RESOURCE_EXHAUSTED"}}),
])
def test_gemini_error_does_not_write_messages(client, gemini, error):
    gemini.side_effect = error
    response = client.post("/api/messages", json={"text": "Требования к документам"})
    assert response.status_code == 502
    assert "Gemini" in response.json()["detail"]
    assert "internal" not in response.text
    assert client.get("/api/messages").json() == []


def test_invalid_gemini_response_does_not_write_messages(client, gemini):
    gemini.return_value = gemini_response({"issues": [{"severity": "unknown", "text": "Замечание"}]})
    assert client.post("/api/messages", json={"text": "Проверь требования"}).status_code == 502
    assert client.get("/api/messages").json() == []


def test_missing_gemini_key(client, gemini, monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", None)
    assert client.post("/api/messages", json={"text": "Проверь требования"}).status_code == 503
    gemini.assert_not_awaited()
    assert client.get("/api/messages").json() == []
