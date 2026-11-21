from typing import Any

from sqlalchemy import Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class RequirementsVersion(Base):
    __tablename__ = "requirements_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    next_requirement_number: Mapped[int] = mapped_column(Integer, nullable=False)
