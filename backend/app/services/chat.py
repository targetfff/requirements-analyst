from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.repositories.conversation import ConversationRepository
from app.schemas.chat import (
    BusinessAnalysis,
    ChatTurn,
    ConversationRead,
    MessageCreate,
    Mode,
    RequirementsEdit,
)
from app.services.analyst import respond
from app.services.history import MAX_CONTEXT_MESSAGES, read_message, recent_context
from app.services.requirements import initial_requirements, read_requirements, revise_requirements

SYSTEM_ANALYSIS_REQUEST = "Подготовь системный анализ по актуальным бизнес-требованиям."


class ConversationConflict(Exception):
    pass


async def read_conversation(db: AsyncSession) -> ConversationRead:
    repository = ConversationRepository(db)
    async with db.begin():
        await repository.read_revision()
        current = read_requirements(await repository.current_requirements())
        rows = await repository.list_messages()

    version = current.version if current is not None else None
    messages = [read_message(message, version) for message in rows]
    return ConversationRead(messages=messages, requirements=current)


async def create_turn(db: AsyncSession, payload: MessageCreate) -> ChatTurn:
    repository = ConversationRepository(db)
    async with db.begin():
        revision = await repository.read_revision()
        saved = await repository.current_requirements()
        current = read_requirements(saved)
        if payload.mode == Mode.system_analyst and (current is None or not current.requirements):
            raise ConversationConflict("Сначала сформируйте актуальные бизнес-требования.")
        if payload.update_requirements and current is None:
            raise ConversationConflict("Сначала выполните бизнес-анализ.")
        rows = await repository.recent_messages(MAX_CONTEXT_MESSAGES)
        if saved is not None:
            db.expunge(saved)

    text = payload.text or SYSTEM_ANALYSIS_REQUEST
    answer = await respond(
        text,
        payload.mode,
        recent_context(rows),
        current,
        update_requirements=payload.update_requirements,
    )
    new_version = None
    analysis = None
    requires_edit_clarification = False
    if isinstance(answer, RequirementsEdit):
        assert saved is not None
        new_version = revise_requirements(saved, answer)
        content = answer.reply
        requires_edit_clarification = answer.decision == "clarify"
        if answer.decision == "apply" and new_version is None:
            content = "Актуальные требования уже соответствуют запросу."
    elif isinstance(answer, str):
        content = answer
    else:
        content = "Анализ требований завершён."
        analysis = answer.model_dump(mode="json")
        if isinstance(answer, BusinessAnalysis) and current is None:
            new_version = initial_requirements(answer)

    async with db.begin():
        # Compare-and-swap so another turn or a history reset cannot invalidate this reply.
        if not await repository.advance_revision(expected=revision):
            raise ConversationConflict(
                "Диалог изменился во время запроса. Повторите отправку."
            )

        if new_version is not None:
            await repository.save_requirements(new_version)
            current = read_requirements(new_version)

        version = current.version if current is not None else None
        user = Message(role="user", mode=payload.mode.value, content=text)
        assistant = Message(
            role="assistant",
            mode=payload.mode.value,
            content=content,
            analysis=analysis,
            requirements_version=version,
        )
        await repository.save_turn(user, assistant)
        return ChatTurn(
            user=read_message(user, version),
            assistant=read_message(assistant, version),
            requirements=current,
            requires_edit_clarification=requires_edit_clarification,
        )


async def clear_messages(db: AsyncSession) -> None:
    repository = ConversationRepository(db)
    async with db.begin():
        await repository.advance_revision()
        await repository.clear()
