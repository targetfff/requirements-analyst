from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Mode(str, Enum):
    analyst = "analyst"
    critic = "critic"
    qa = "qa"


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    mode: Mode = Mode.analyst

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Сообщение не может состоять только из пробелов")
        return value


class Requirement(BaseModel):
    id: str
    text: str


class Issue(BaseModel):
    severity: Literal["low", "medium", "high"]
    text: str


class AnalysisResult(BaseModel):
    # Fixed fixture for UI development, NOT an LLM response.
    is_demo: bool = True
    requirements: list[Requirement] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    mode: Mode
    content: str
    analysis: AnalysisResult | None = None
    created_at: datetime


class ChatTurn(BaseModel):
    user: MessageRead
    assistant: MessageRead
