import json

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.schemas.chat import MessageRead, SystemMessageRead

MAX_CONTEXT_MESSAGES = 24
MAX_HISTORY_CHARS = 16_000
MAX_MESSAGE_CHARS = 3_000
message_adapter = TypeAdapter(MessageRead)


def list_messages(db: Session) -> list[Message]:
    return list(db.scalars(select(Message).order_by(Message.id.asc())))


def read_message(message: Message, current_version: int | None) -> MessageRead:
    result = message_adapter.validate_python(message)
    if isinstance(result, SystemMessageRead) and result.analysis is not None:
        result.is_stale = current_version is not None and result.requirements_version != current_version
    return result


def recent_context(db: Session) -> list[HumanMessage | AIMessage]:
    rows = list(db.scalars(select(Message).order_by(Message.id.desc()).limit(MAX_CONTEXT_MESSAGES)))
    messages = []
    chars = 0
    # Select complete turns from newest to oldest and send them in dialogue order.
    for assistant, user in zip(rows[::2], rows[1::2]):
        if assistant.role != "assistant" or user.role != "user":
            break
        answer = assistant.content
        if assistant.analysis:
            version = assistant.requirements_version or "не зафиксирована"
            answer += f"\nАнализ {assistant.mode}, версия требований: {version}\n"
            answer += json.dumps(assistant.analysis, ensure_ascii=False, separators=(",", ":"))
        if len(answer) > MAX_MESSAGE_CHARS:
            answer = answer[:MAX_MESSAGE_CHARS] + "\n[Старый ответ сокращён]"
        chars += len(answer) + len(user.content)
        if chars > MAX_HISTORY_CHARS:
            break
        messages.extend((AIMessage(content=answer), HumanMessage(content=user.content)))
    return list(reversed(messages))
