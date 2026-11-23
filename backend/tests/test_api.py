import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import ClientError
from google.genai.models import AsyncModels
from google.genai.types import GenerateContentResponse
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.conversation import RequirementsVersion
from app.prompts.business_analyst import BUSINESS_ANALYST_PROMPT
from app.prompts.clarification import CLARIFICATION_PROMPT, DIALOGUE_PROMPT
from app.prompts.system_analyst import SYSTEM_ANALYST_PROMPT
from app.schemas.chat import InitialBusinessAnalysis, SystemAnalysis
from app.services.chat import SYSTEM_ANALYSIS_REQUEST
from app.services.llm import get_llm

pytestmark = pytest.mark.anyio

BUSINESS_RESULT = {
    "feasibility": {
        "assessment": "Загрузка документов реализуема",
        "facts": ["Нужна загрузка документов"],
        "assumptions": ["Объём документов пока неизвестен"],
    },
    "issues": [{"severity": "medium", "text": "Не заданы форматы файлов"}],
    "questions": ["Какие форматы поддержать?", "Кто может загружать документы?"],
    "requirements": [{"id": "FR-01", "text": "Пользователь может загрузить документ"}],
    "acceptance_criteria": ["FR-01: загруженный документ появляется в списке"],
    "mvp": ["Загрузка документа и просмотр списка"],
}

SYSTEM_RESULT = {
    "feasibility": {
        "assessment": "Достаточно одного приложения",
        "facts": ["Нужна загрузка документов"],
        "assumptions": ["Нагрузка невысокая"],
    },
    "issues": [],
    "questions": ["Каков размер файлов?", "Сколько запросов ожидается в пиковые часы?"],
    "architecture": "Модульный монолит на Python",
    "technologies": [{"name": "FastAPI", "rationale": "Валидация входных данных"}],
    "data_models": [{"name": "Document", "description": "Идентификатор и имя файла"}],
    "api_endpoints": [{"method": "POST", "path": "/documents", "purpose": "Загрузка файла"}],
    "component_interactions": ["API передаёт загруженный файл в хранилище"],
}

EDIT_RESULT = {
    "reply": "Ограничил загрузку файлами PDF.",
    "decision": "apply",
    "changes": [{"operation": "replace", "id": "FR-01", "text": "Загрузка только PDF"}],
    "acceptance_criteria": ["FR-01: файл другого формата отклоняется"],
    "mvp": None,
}


def gemini_response(analysis: dict[str, Any] | str) -> GenerateContentResponse:
    text = analysis if isinstance(analysis, str) else json.dumps(analysis)
    content = {"role": "model", "parts": [{"text": text}]}
    return GenerateContentResponse.model_validate({
        "candidates": [{"content": content}],
    })


@pytest.fixture(autouse=True)
def gemini(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    generate = AsyncMock(return_value=gemini_response(BUSINESS_RESULT))
    monkeypatch.setattr(settings, "google_api_key", SecretStr("test-gemini-key"))
    monkeypatch.setattr(AsyncModels, "generate_content", generate)
    get_llm.cache_clear()
    yield generate
    get_llm.cache_clear()


async def test_health(anonymous_client: AsyncClient) -> None:
    res = await anonymous_client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "llm"}


async def test_default_mode_and_reload_history(client: AsyncClient) -> None:
    response = await client.post(
        "/api/messages",
        json={"text": "  Нужна загрузка документов  "},
    )
    assert response.status_code == 201
    turn = response.json()
    assert turn["user"]["content"] == "Нужна загрузка документов"
    assert turn["user"]["mode"] == turn["assistant"]["mode"] == "business_analyst"
    assert turn["user"]["analysis"] is None
    assert turn["assistant"]["analysis"] == BUSINESS_RESULT
    assert turn["requirements"]["version"] == 1
    assert turn["requirements"]["requirements"] == BUSINESS_RESULT["requirements"]

    history = (await client.get("/api/messages")).json()
    assert history == [turn["user"], turn["assistant"]]


@pytest.mark.parametrize("mode,prompt,schema,analysis", [
    ("business_analyst", BUSINESS_ANALYST_PROMPT, InitialBusinessAnalysis, BUSINESS_RESULT),
    ("system_analyst", SYSTEM_ANALYST_PROMPT, SystemAnalysis, SYSTEM_RESULT),
], ids=["business", "system"])
async def test_modes(
    client: AsyncClient,
    gemini: AsyncMock,
    mode: str,
    prompt: str,
    schema: type[InitialBusinessAnalysis] | type[SystemAnalysis],
    analysis: dict[str, Any],
) -> None:
    if mode == "system_analyst":
        response = await client.post("/api/messages", json={"text": "Идея для истории"})
        assert response.status_code == 201
        gemini.reset_mock()
    gemini.return_value = gemini_response(analysis)
    text = "Проверь требования {к документам}"
    payload = {"mode": mode} if mode == "system_analyst" else {"text": text, "mode": mode}
    response = await client.post("/api/messages", json=payload)
    assert response.status_code == 201
    turn = response.json()
    assert turn["user"]["mode"] == turn["assistant"]["mode"] == mode
    assert turn["assistant"]["analysis"] == analysis
    assert (await client.get("/api/messages")).json()[-2:] == [turn["user"], turn["assistant"]]

    gemini.assert_awaited_once()
    request = gemini.await_args.kwargs
    config = request["config"]
    assert config.system_instruction.parts[0].text == f"{prompt}\n\n{DIALOGUE_PROMPT}"
    message = request["contents"][-1]
    assert message.role == "user"
    if mode == "system_analyst":
        assert turn["user"]["content"] == SYSTEM_ANALYSIS_REQUEST
        assert turn["assistant"]["requirements_version"] == 1
        assert message.parts[0].text.endswith(SYSTEM_ANALYSIS_REQUEST)
    else:
        assert message.parts[0].text == text
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema.model_json_schema()


async def test_invalid_messages_are_rejected(client: AsyncClient, gemini: AsyncMock) -> None:
    for invalid_text in ["   ", "", "x" * 501]:
        assert (await client.post("/api/messages", json={"text": invalid_text})).status_code == 422
    for mode in ("analyst", "critic", "qa", "other"):
        response = await client.post("/api/messages", json={"text": "ok", "mode": mode})
        assert response.status_code == 422
    assert (await client.post("/api/messages", json={"mode": "system_analyst"})).status_code == 409
    invalid_edit = {"text": "copy", "update_requirements": True}
    assert (await client.post("/api/messages", json=invalid_edit)).status_code == 422
    gemini.assert_not_awaited()
    assert (await client.get("/api/messages")).json() == []


async def test_clear_history(client: AsyncClient) -> None:
    assert (await client.post("/api/messages", json={"text": "Hello"})).status_code == 201
    assert (await client.delete("/api/messages")).status_code == 204
    assert (await client.get("/api/messages")).json() == []
    assert (await client.get("/api/chat")).json() == {"messages": [], "requirements": None}


async def test_gemini_error_does_not_write_messages(
    client: AsyncClient,
    gemini: AsyncMock,
) -> None:
    assert (await client.post("/api/messages", json={"text": "Нужна загрузка"})).status_code == 201
    before = (await client.get("/api/chat")).json()
    gemini.side_effect = ClientError(
        429, {"error": {"message": "internal quota error", "status": "RESOURCE_EXHAUSTED"}},
    )
    response = await client.post("/api/messages", json={
        "text": "Разрешить только PDF", "mode": "clarification", "update_requirements": True,
    })
    assert response.status_code == 502
    assert "Gemini" in response.json()["detail"]
    assert "internal" not in response.text
    assert (await client.get("/api/chat")).json() == before


async def test_invalid_gemini_response_does_not_write_messages(
    client: AsyncClient,
    gemini: AsyncMock,
) -> None:
    before = (await client.get("/api/chat")).json()
    gemini.return_value = gemini_response({
        **BUSINESS_RESULT,
        "requirements": [
            *BUSINESS_RESULT["requirements"], {"id": "FR-03", "text": "Просмотр документов"},
        ],
    })
    response = await client.post(
        "/api/messages", json={"text": "Проверь требования", "mode": "business_analyst"},
    )
    assert response.status_code == 502
    assert (await client.get("/api/chat")).json() == before


async def test_missing_gemini_key(
    client: AsyncClient,
    gemini: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_api_key", None)
    assert (await client.post("/api/messages", json={"text": "Проверь требования"})).status_code == 503
    gemini.assert_not_awaited()
    assert (await client.get("/api/messages")).json() == []


async def test_clarification_preserves_context_without_duplicating_current_message(
    client: AsyncClient,
    gemini: AsyncMock,
) -> None:
    assert (await client.post("/api/messages", json={"text": "Идея с файлами"})).status_code == 201
    gemini.return_value = gemini_response("Можно начать с ограничения в 10 МБ.")
    text = "Какой размер выбрать?"
    turn = (await client.post("/api/messages", json={"text": text, "mode": "clarification"})).json()
    assert turn["assistant"]["content"] == "Можно начать с ограничения в 10 МБ."
    assert turn["assistant"]["analysis"] is None
    assert turn["requirements"]["version"] == 1
    request = gemini.await_args.kwargs
    assert request["config"].response_json_schema is None
    assert request["config"].system_instruction.parts[0].text.startswith(CLARIFICATION_PROMPT)
    messages = request["contents"]
    assert [message.role for message in messages] == ["user", "model", "user"]
    assert messages[0].parts[0].text == "Идея с файлами"
    assert "FR-01" in messages[1].parts[0].text
    assert messages[2].parts[0].text.count(text) == 1


async def test_edit_requirements_and_repeat_system_analysis(
    client: AsyncClient, gemini: AsyncMock, db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert (await client.post("/api/messages", json={"text": "Нужна загрузка"})).status_code == 201
    gemini.return_value = gemini_response(SYSTEM_RESULT)
    first = (await client.post("/api/messages", json={"mode": "system_analyst"})).json()["assistant"]
    assert first["requirements_version"] == 1 and first["is_stale"] is False

    gemini.return_value = gemini_response(EDIT_RESULT)
    edited = (await client.post("/api/messages", json={
        "mode": "clarification", "text": "Разрешить только PDF", "update_requirements": True,
    })).json()
    assert edited["assistant"]["content"] == EDIT_RESULT["reply"]
    assert edited["requirements"]["version"] == 2
    assert edited["requirements"]["requirements"] == [{"id": "FR-01", "text": "Загрузка только PDF"}]
    async with db_sessions() as db:
        versions = (
            await db.scalars(select(RequirementsVersion).order_by(RequirementsVersion.version))
        ).all()
        assert [version.version for version in versions] == [1, 2]
        assert versions[0].document["requirements"] == BUSINESS_RESULT["requirements"]

    gemini.return_value = gemini_response(SYSTEM_RESULT)
    second = (await client.post("/api/messages", json={"mode": "system_analyst"})).json()["assistant"]
    assert second["requirements_version"] == 2 and second["is_stale"] is False
    current_prompt = gemini.await_args.kwargs["contents"][-1].parts[0].text
    assert '"version":2' in current_prompt
    assert "Загрузка только PDF" in current_prompt
    history = {message["id"]: message for message in (await client.get("/api/messages")).json()}
    assert history[first["id"]]["is_stale"] is True
    assert history[first["id"]]["analysis"] == first["analysis"]


@pytest.mark.parametrize("suggest_changes", [False, True], ids=["id-gap", "different-document"])
async def test_repeat_business_analysis_uses_saved_document(
    client: AsyncClient,
    gemini: AsyncMock,
    suggest_changes: bool,
) -> None:
    initial = [
        *BUSINESS_RESULT["requirements"],
        {"id": "FR-02", "text": "Удаление документов"},
        {"id": "FR-03", "text": "Просмотр документов"},
    ]
    gemini.return_value = gemini_response({**BUSINESS_RESULT, "requirements": initial})
    created = await client.post("/api/messages", json={"text": "Нужна работа с документами"})
    assert created.status_code == 201

    document = {
        "requirements": [initial[0], initial[2]],
        "acceptance_criteria": ["FR-03: документ доступен для просмотра"],
        "mvp": ["Загрузка и просмотр документов"],
    }
    gemini.return_value = gemini_response({
        **EDIT_RESULT,
        "reply": "Удалил требование FR-02.",
        "changes": [{"operation": "remove", "id": "FR-02", "text": None}],
        "acceptance_criteria": document["acceptance_criteria"],
        "mvp": document["mvp"],
    })
    edited = await client.post("/api/messages", json={
        "mode": "clarification", "text": "Убери удаление документов", "update_requirements": True,
    })
    assert edited.status_code == 201
    saved = {"version": 2, **document}
    assert edited.json()["requirements"] == saved

    suggested = document
    if suggest_changes:
        suggested = {
            "requirements": [{"id": "FR-01", "text": "Архивация документов"}],
            "acceptance_criteria": ["FR-01: документ добавлен в архив"],
            "mvp": ["Архив документов"],
        }
    review = {
        "feasibility": {
            "assessment": "Загрузка и просмотр реализуемы",
            "facts": ["Удаление документов исключено"],
            "assumptions": [],
        },
        "issues": [{"severity": "medium", "text": "Не согласованы права просмотра"}],
        "questions": ["Кто может просматривать документы?", "Как долго хранить документы?"],
    }
    gemini.return_value = gemini_response(review | suggested)
    repeated = await client.post("/api/messages", json={
        "mode": "business_analyst", "text": "Повтори бизнес-анализ по актуальным требованиям",
    })
    assert repeated.status_code == 201
    turn = repeated.json()
    assert turn["requirements"] == saved
    assert turn["assistant"]["requirements_version"] == 2
    assert turn["assistant"]["analysis"] == review | document
    schema = gemini.await_args.kwargs["config"].response_json_schema
    assert schema["properties"].keys() == review.keys()

    history = await client.get("/api/chat")
    assert history.status_code == 200
    assert history.json()["requirements"] == saved
    assert history.json()["messages"][-1] == turn["assistant"]


async def test_ambiguous_edit_requests_clarification_without_changing_requirements(
    client: AsyncClient,
    gemini: AsyncMock,
) -> None:
    first = (await client.post("/api/messages", json={"text": "Идея"})).json()["requirements"]
    gemini.return_value = gemini_response({
        "decision": "clarify", "reply": "Какой формат вы имеете в виду?",
        "changes": [], "acceptance_criteria": None, "mvp": None,
    })
    payload = {"mode": "clarification", "text": "Поменяй формат", "update_requirements": True}
    turn = (await client.post("/api/messages", json=payload)).json()
    assert turn["requirements"] == first
    assert turn["requires_edit_clarification"] is True
    assert turn["assistant"]["content"].endswith("?")
