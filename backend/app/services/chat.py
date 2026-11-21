from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.schemas.chat import AnalysisResult, MessageCreate
from app.services.analyst import analyze


def list_messages(db: Session) -> list[Message]:
    return list(db.scalars(select(Message).order_by(Message.id.asc())))


async def create_turn(db: Session, payload: MessageCreate) -> tuple[Message, Message]:
    result = await analyze(payload.text, payload.mode)
    analysis = AnalysisResult(is_demo=False, **result.model_dump())
    user_message = Message(role="user", mode=payload.mode.value, content=payload.text)
    assistant_message = Message(
        role="assistant",
        mode=payload.mode.value,
        content="Анализ требований завершён.",
        analysis=analysis.model_dump(mode="json"),
    )
    try:
        db.add_all([user_message, assistant_message])
        db.commit()
        db.refresh(user_message)
        db.refresh(assistant_message)
    except Exception:
        db.rollback()
        raise
    return user_message, assistant_message


def clear_messages(db: Session) -> None:
    db.execute(delete(Message))
    db.commit()
