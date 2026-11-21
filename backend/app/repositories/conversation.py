from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, RequirementsVersion
from app.models.message import Message

CONVERSATION_ID = 1


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read_revision(self) -> int:
        statement = (
            select(Conversation.revision)
            .where(Conversation.id == CONVERSATION_ID)
            .with_for_update(read=True)
        )
        return (await self.session.execute(statement)).scalar_one()

    async def advance_revision(self, expected: int | None = None) -> bool:
        statement = update(Conversation).where(Conversation.id == CONVERSATION_ID)
        if expected is not None:
            statement = statement.where(Conversation.revision == expected)
        result = await self.session.execute(
            statement.values(revision=Conversation.revision + 1).returning(Conversation.revision)
        )
        return result.scalar_one_or_none() is not None

    async def current_requirements(self) -> RequirementsVersion | None:
        statement = select(RequirementsVersion).order_by(RequirementsVersion.version.desc()).limit(1)
        return await self.session.scalar(statement)

    async def list_messages(self) -> Sequence[Message]:
        statement = select(Message).order_by(Message.id.asc())
        return (await self.session.scalars(statement)).all()

    async def recent_messages(self, limit: int) -> Sequence[Message]:
        statement = select(Message).order_by(Message.id.desc()).limit(limit)
        return (await self.session.scalars(statement)).all()

    async def save_requirements(self, version: RequirementsVersion) -> None:
        self.session.add(version)
        await self.session.flush()

    async def save_turn(self, user: Message, assistant: Message) -> None:
        self.session.add_all([user, assistant])
        await self.session.flush()

    async def clear(self) -> None:
        await self.session.execute(delete(Message))
        await self.session.execute(delete(RequirementsVersion))
