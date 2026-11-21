from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.session import require_session
from app.db.session import get_db
from app.schemas.chat import ChatTurn, ConversationRead, MessageCreate, MessageRead
from app.services.analyst import AnalysisError
from app.services.chat import ConversationConflict, clear_messages, create_turn, read_conversation
from app.services.llm import LLMNotConfiguredError
from app.services.requirements import InvalidRequirementsEdit

router = APIRouter(prefix="/api", tags=["chat"])
chat_router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "llm"}


@chat_router.get("/messages", response_model=list[MessageRead])
def get_messages(db: Session = Depends(get_db)):
    return read_conversation(db).messages


@chat_router.get("/chat", response_model=ConversationRead)
def get_conversation(db: Session = Depends(get_db)) -> ConversationRead:
    return read_conversation(db)


@chat_router.post("/messages", response_model=ChatTurn, status_code=status.HTTP_201_CREATED)
async def post_message(payload: MessageCreate, db: Session = Depends(get_db)) -> ChatTurn:
    try:
        return await create_turn(db, payload)
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
    except InvalidRequirementsEdit as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ConversationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@chat_router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
def delete_messages(db: Session = Depends(get_db)) -> Response:
    clear_messages(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(chat_router)
