from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.chat import ChatTurn, MessageCreate, MessageRead
from app.services.chat import clear_messages, create_turn, list_messages

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "demo"}


@router.get("/messages", response_model=list[MessageRead])
def get_messages(db: Session = Depends(get_db)):
    return list_messages(db)


@router.post("/messages", response_model=ChatTurn, status_code=status.HTTP_201_CREATED)
def post_message(payload: MessageCreate, db: Session = Depends(get_db)):
    user_message, assistant_message = create_turn(db, payload)
    return ChatTurn(
        user=MessageRead.model_validate(user_message),
        assistant=MessageRead.model_validate(assistant_message),
    )


@router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
def delete_messages(db: Session = Depends(get_db)) -> Response:
    clear_messages(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
