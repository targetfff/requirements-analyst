from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, RequirementsVersion
from app.models.message import Message
from app.schemas.chat import (
    BusinessAnalysis, ChatTurn, ConversationRead, MessageCreate, Mode, RequirementsEdit,
)
from app.services.analyst import respond
from app.services.history import list_messages, read_message, recent_context
from app.services.requirements import (
    current_requirements, initial_requirements, read_requirements, revise_requirements,
)

CONVERSATION_ID = 1
SYSTEM_ANALYSIS_REQUEST = "Подготовь системный анализ по актуальным бизнес-требованиям."


class ConversationConflict(Exception):
    pass


def read_conversation(db: Session) -> ConversationRead:
    with db.begin():
        db.execute(select(Conversation.id).where(Conversation.id == CONVERSATION_ID).with_for_update(read=True))
        current = read_requirements(current_requirements(db))
        version = current.version if current is not None else None
        messages = [read_message(message, version) for message in list_messages(db)]
        return ConversationRead(messages=messages, requirements=current)


async def create_turn(db: Session, payload: MessageCreate) -> ChatTurn:
    with db.begin():
        revision = db.execute(select(Conversation.revision).where(Conversation.id == CONVERSATION_ID)).scalar_one()
        saved = current_requirements(db)
        current = read_requirements(saved)
        if payload.mode == Mode.system_analyst and (current is None or not current.requirements):
            raise ConversationConflict("Сначала сформируйте актуальные бизнес-требования.")
        if payload.update_requirements and current is None:
            raise ConversationConflict("Сначала выполните бизнес-анализ.")
        history = recent_context(db)
        if saved is not None:
            db.expunge(saved)

    text = payload.text or SYSTEM_ANALYSIS_REQUEST
    answer = await respond(text, payload.mode, history, current, update_requirements=payload.update_requirements)
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

    with db.begin():
        # Compare-and-swap so another turn or a history reset cannot invalidate this reply.
        changed = db.execute(
            update(Conversation)
            .where(Conversation.id == CONVERSATION_ID, Conversation.revision == revision)
            .values(revision=revision + 1)
        )
        if changed.rowcount != 1:
            raise ConversationConflict("Диалог изменился во время запроса. Повторите отправку.")

        if new_version is not None:
            db.add(new_version)
            db.flush()
            current = read_requirements(new_version)

        version = current.version if current is not None else None
        user = Message(role="user", mode=payload.mode.value, content=text)
        assistant = Message(
            role="assistant", mode=payload.mode.value, content=content,
            analysis=analysis, requirements_version=version,
        )
        db.add_all([user, assistant])
        db.flush()
        return ChatTurn(
            user=read_message(user, version), assistant=read_message(assistant, version), requirements=current,
            requires_edit_clarification=requires_edit_clarification,
        )


def clear_messages(db: Session) -> None:
    with db.begin():
        db.execute(
            update(Conversation).where(Conversation.id == CONVERSATION_ID)
            .values(revision=Conversation.revision + 1)
        )
        db.execute(delete(Message))
        db.execute(delete(RequirementsVersion))
