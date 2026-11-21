from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.chat import ChatTurn, MessageCreate, MessageRead
from app.services.analyst import AnalysisError
from app.services.chat import clear_messages, create_turn, list_messages
from app.services.llm import LLMNotConfiguredError

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "llm"}


@router.get("/messages", response_model=list[MessageRead])
def get_messages(db: Session = Depends(get_db)):
    return list_messages(db)


@router.post("/messages", response_model=ChatTurn, status_code=status.HTTP_201_CREATED)
async def post_message(payload: MessageCreate, db: Session = Depends(get_db)):
    try:
        user_message, assistant_message = await create_turn(db, payload)
    except LLMNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini не настроен. Укажите ключ API на сервере.",
        ) from exc
    except AnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось получить результат анализа от Gemini. Попробуйте ещё раз.",
        ) from exc
    return ChatTurn(
        user=MessageRead.model_validate(user_message),
        assistant=MessageRead.model_validate(assistant_message),
    )


@router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
def delete_messages(db: Session = Depends(get_db)) -> Response:
    clear_messages(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
