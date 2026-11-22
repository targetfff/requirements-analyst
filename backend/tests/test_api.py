import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from google.genai.errors import ClientError
from google.genai.models import AsyncModels
from google.genai.types import GenerateContentResponse
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes import router
from app.core.config import settings
from app.db.dependencies import get_db
from app.db.session import create_runtime_engine
from app.models.conversation import Conversation, RequirementsVersion
from app.models.message import Message
from app.prompts.business_analyst import BUSINESS_ANALYST_PROMPT
from app.prompts.clarification import CLARIFICATION_PROMPT, DIALOGUE_PROMPT
from app.prompts.system_analyst import SYSTEM_ANALYST_PROMPT
from app.schemas.chat import BusinessAnalysis, SystemAnalysis
from app.services.chat import SYSTEM_ANALYSIS_REQUEST
from app.services.history import MAX_CONTEXT_MESSAGES
from app.services.llm import get_llm
from tests.conftest import TEST_ORIGIN

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
    ("business_analyst", BUSINESS_ANALYST_PROMPT, BusinessAnalysis, BUSINESS_RESULT),
    ("system_analyst", SYSTEM_ANALYST_PROMPT, SystemAnalysis, SYSTEM_RESULT),
], ids=["business", "system"])
async def test_modes(
    client: AsyncClient,
    gemini: AsyncMock,
    mode: str,
    prompt: str,
    schema: type[BusinessAnalysis] | type[SystemAnalysis],
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


async def test_legacy_history(
    client: AsyncClient, db_sessions: async_sessionmaker[AsyncSession], gemini: AsyncMock,
) -> None:
    legacy_results = {
        "analyst": {"is_demo": True, "requirements": [{"id": "FR-01", "text": "Загрузка"}]},
        "critic": {"is_demo": False, "issues": [{"severity": "low", "text": "Нет форматов"}]},
        "qa": {"is_demo": False, "acceptance_criteria": ["Файл отображается в списке"]},
    }
    async with db_sessions.begin() as db:
        for mode, analysis in legacy_results.items():
            db.add(Message(role="user", mode=mode, content="Старый запрос"))
            db.add(Message(role="assistant", mode=mode, content="Старый ответ", analysis=analysis))

    response = await client.get("/api/messages")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 6
    for (mode, analysis), user, assistant in zip(legacy_results.items(), history[::2], history[1::2]):
        assert user["role"] == "user" and assistant["role"] == "assistant"
        assert user["mode"] == assistant["mode"] == mode
        assert user["analysis"] is None
        assert {key: assistant["analysis"][key] for key in analysis} == analysis
        assert "feasibility" not in assistant["analysis"]
    gemini.assert_not_awaited()


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


@pytest.mark.parametrize("error", [
    TimeoutError("internal timeout"),
    ClientError(429, {"error": {"message": "internal quota error", "status": "RESOURCE_EXHAUSTED"}}),
])
async def test_gemini_error_does_not_write_messages(
    client: AsyncClient,
    gemini: AsyncMock,
    error: Exception,
) -> None:
    assert (await client.post("/api/messages", json={"text": "Нужна загрузка"})).status_code == 201
    before = (await client.get("/api/chat")).json()
    gemini.side_effect = error
    response = await client.post("/api/messages", json={
        "text": "Разрешить только PDF", "mode": "clarification", "update_requirements": True,
    })
    assert response.status_code == 502
    assert "Gemini" in response.json()["detail"]
    assert "internal" not in response.text
    assert (await client.get("/api/chat")).json() == before


@pytest.mark.parametrize("mode,analysis", [
    ("business_analyst", {**BUSINESS_RESULT, "questions": ["Один вопрос?"]}),
    ("business_analyst", {**BUSINESS_RESULT, "requirements": [{"id": "FR-02", "text": "Загрузка"}]}),
    ("business_analyst", {**BUSINESS_RESULT, "issues": [{"severity": "unknown", "text": "Риск"}]}),
    ("system_analyst", {**SYSTEM_RESULT, "questions": [f"Вопрос {i}?" for i in range(6)]}),
    ("system_analyst", {**SYSTEM_RESULT, "questions": ["Каков размер файлов?", "  "]}),
    ("system_analyst", BUSINESS_RESULT),
])
async def test_invalid_gemini_response_does_not_write_messages(
    client: AsyncClient,
    gemini: AsyncMock,
    mode: str,
    analysis: dict[str, Any],
) -> None:
    if mode == "system_analyst":
        assert (await client.post("/api/messages", json={"text": "Идея"})).status_code == 201
    before = (await client.get("/api/chat")).json()
    gemini.return_value = gemini_response(analysis)
    response = await client.post("/api/messages", json={"text": "Проверь требования", "mode": mode})
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


async def test_ambiguous_edit_and_failed_edit_do_not_modify_state(
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

    before = (await client.get("/api/chat")).json()
    gemini.return_value = gemini_response({
        **EDIT_RESULT, "changes": [{"operation": "remove", "id": "FR-99", "text": None}],
    })
    assert (await client.post("/api/messages", json=payload)).status_code == 502
    assert (await client.get("/api/chat")).json() == before


async def test_removed_requirement_id_is_not_reused(client: AsyncClient, gemini: AsyncMock) -> None:
    assert (await client.post("/api/messages", json={"text": "Идея"})).status_code == 201
    payload = {"mode": "clarification", "text": "Обновить список", "update_requirements": True}
    add = {"operation": "add", "id": None, "text": "Просмотр списка документов"}
    remove = {"operation": "remove", "id": "FR-02", "text": None}
    base = {**EDIT_RESULT, "acceptance_criteria": None}
    for change, expected in (
        (add, ["FR-01", "FR-02"]), (remove, ["FR-01"]), (add, ["FR-01", "FR-03"]),
    ):
        gemini.return_value = gemini_response({**base, "changes": [change]})
        response = await client.post("/api/messages", json=payload)
        assert response.status_code == 201
        requirements = response.json()["requirements"]["requirements"]
        assert [requirement["id"] for requirement in requirements] == expected
        assert requirements[0] == BUSINESS_RESULT["requirements"][0]


async def test_context_limit_keeps_current_requirements_after_restart(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_sessions: async_sessionmaker[AsyncSession],
    gemini: AsyncMock,
) -> None:
    assert (await client.post("/api/messages", json={"text": "Исходная идея"})).status_code == 201
    gemini.return_value = gemini_response(EDIT_RESULT)
    edit = {"text": "Разрешить только PDF", "mode": "clarification", "update_requirements": True}
    assert (await client.post("/api/messages", json=edit)).json()["requirements"]["version"] == 2
    async with db_sessions.begin() as db:
        for i in range(MAX_CONTEXT_MESSAGES):
            db.add(Message(role="user", mode="clarification", content=f"Вопрос {i}"))
            db.add(Message(role="assistant", mode="clarification", content=f"Ответ {i}"))
    previous = (await client.get("/api/chat")).json()
    restarted_engine = create_runtime_engine(db_engine.url)
    restarted_sessions = async_sessionmaker(restarted_engine, expire_on_commit=False)
    restarted = FastAPI()
    restarted.include_router(router)

    async def restart_db() -> AsyncIterator[AsyncSession]:
        async with restarted_sessions() as db:
            yield db

    restarted.dependency_overrides[get_db] = restart_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=restarted),
            base_url=TEST_ORIGIN,
            headers={"Origin": TEST_ORIGIN},
            cookies=client.cookies,
        ) as new_client:
            assert (await new_client.get("/api/chat")).json() == previous
            gemini.return_value = gemini_response("Уточнение после перезапуска")
            payload = {"text": "После перезапуска", "mode": "clarification"}
            assert (await new_client.post("/api/messages", json=payload)).status_code == 201
    finally:
        await restarted_engine.dispose()
    context = gemini.await_args.kwargs["contents"]
    assert len(context) == MAX_CONTEXT_MESSAGES + 1
    assert not any("Исходная идея" in message.parts[0].text for message in context)
    assert '"version":2' in context[-1].parts[0].text
    assert "Загрузка только PDF" in context[-1].parts[0].text


async def test_reset_during_model_call_discards_obsolete_reply(
    client: AsyncClient, gemini: AsyncMock,
) -> None:
    assert (await client.post("/api/messages", json={"text": "Идея"})).status_code == 201

    async def reset_chat(**_kwargs: Any) -> GenerateContentResponse:
        response = await client.delete("/api/messages")
        assert response.status_code == 204
        return gemini_response("Ответ на уже удалённый вопрос")

    gemini.side_effect = reset_chat
    payload = {"text": "Уточнение", "mode": "clarification"}
    assert (await client.post("/api/messages", json=payload)).status_code == 409
    assert (await client.get("/api/chat")).json() == {"messages": [], "requirements": None}


async def test_parallel_turns_use_one_revision(client: AsyncClient, gemini: AsyncMock) -> None:
    first_entered_gemini = asyncio.Event()
    release_first = asyncio.Event()

    async def answer(**kwargs: Any) -> GenerateContentResponse:
        if kwargs["contents"][-1].parts[0].text == "First idea":
            first_entered_gemini.set()
            await release_first.wait()
        return gemini_response(BUSINESS_RESULT)

    gemini.side_effect = answer
    first = asyncio.create_task(client.post("/api/messages", json={"text": "First idea"}))
    try:
        await asyncio.wait_for(first_entered_gemini.wait(), timeout=5)
        second = await asyncio.wait_for(
            client.post("/api/messages", json={"text": "Second idea"}), timeout=5,
        )
    finally:
        release_first.set()
        rejected = await first

    assert second.status_code == 201
    assert rejected.status_code == 409
    turn = second.json()
    history = (await client.get("/api/chat")).json()
    assert history["messages"] == [turn["user"], turn["assistant"]]
    assert history["requirements"]["version"] == 1


async def test_database_error_rolls_back_revision_requirements_and_messages(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(text("""
            CREATE TRIGGER reject_assistant BEFORE INSERT ON messages
            WHEN NEW.role = 'assistant'
            BEGIN
                SELECT RAISE(ABORT, 'test write failure');
            END
        """))

    with pytest.raises(IntegrityError, match="test write failure"):
        await client.post("/api/messages", json={"text": "Idea"})

    assert (await client.get("/api/chat")).json() == {"messages": [], "requirements": None}
    async with db_sessions() as db:
        assert await db.scalar(select(Conversation.revision)) == 0

    async with db_engine.begin() as connection:
        await connection.execute(text("DROP TRIGGER reject_assistant"))
    retry = await client.post("/api/messages", json={"text": "Idea"})
    assert retry.status_code == 201
    assert retry.json()["requirements"]["version"] == 1
