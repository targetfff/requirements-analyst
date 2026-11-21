from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.schemas.chat import MessageCreate
from app.services.demo_analyst import build_demo_answer


def list_messages(db: Session) -> list[Message]:
    return list(db.scalars(select(Message).order_by(Message.id.asc())))


def create_turn(db: Session, payload: MessageCreate) -> tuple[Message, Message]:
    content, analysis = build_demo_answer(payload.mode)
    user_message = Message(role="user", mode=payload.mode.value, content=payload.text)
    assistant_message = Message(
        role="assistant", mode=payload.mode.value,
        content=content, analysis=analysis.model_dump(mode="json"),
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
