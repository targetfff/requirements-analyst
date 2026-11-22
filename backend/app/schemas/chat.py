from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class Mode(str, Enum):
    business_analyst = "business_analyst"
    system_analyst = "system_analyst"
    clarification = "clarification"


class MessageCreate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    mode: Mode = Mode.business_analyst
    update_requirements: bool = False

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Сообщение не может состоять только из пробелов")
        return value

    @model_validator(mode="after")
    def valid_action(self):
        if self.text is None and self.mode != Mode.system_analyst:
            raise ValueError("Введите сообщение")
        if self.update_requirements and self.mode != Mode.clarification:
            raise ValueError("Изменение требований доступно в режиме уточнения")
        return self


class Requirement(BaseModel):
    id: str
    text: str


class Issue(BaseModel):
    severity: Literal["low", "medium", "high"]
    text: str


NonemptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Feasibility(BaseModel):
    assessment: NonemptyText
    facts: list[NonemptyText]
    assumptions: list[NonemptyText]


class AnalysisBase(BaseModel):
    feasibility: Feasibility
    issues: list[Issue]
    questions: list[NonemptyText] = Field(min_length=2, max_length=5)


class RequirementsDocument(BaseModel):
    requirements: list[Requirement]
    acceptance_criteria: list[NonemptyText]
    mvp: list[NonemptyText]


class RequirementsRead(RequirementsDocument):
    version: int


class BusinessAnalysis(AnalysisBase, RequirementsDocument):
    @field_validator("requirements")
    @classmethod
    def sequential_requirement_ids(cls, requirements: list[Requirement]) -> list[Requirement]:
        for number, requirement in enumerate(requirements, start=1):
            if requirement.id != f"FR-{number:02d}":
                raise ValueError("Требования должны иметь последовательные ID начиная с FR-01")
        return requirements


class Technology(BaseModel):
    name: NonemptyText
    rationale: NonemptyText


class DataModel(BaseModel):
    name: NonemptyText
    description: NonemptyText


class ApiEndpoint(BaseModel):
    method: NonemptyText
    path: NonemptyText
    purpose: NonemptyText


class SystemAnalysis(AnalysisBase):
    architecture: NonemptyText
    technologies: list[Technology]
    data_models: list[DataModel]
    api_endpoints: list[ApiEndpoint]
    component_interactions: list[NonemptyText]


class RequirementChange(BaseModel):
    operation: Literal["add", "replace", "remove"]
    id: str | None
    text: NonemptyText | None


class RequirementsEdit(BaseModel):
    reply: NonemptyText
    decision: Literal["apply", "clarify", "no_change"]
    changes: list[RequirementChange]
    acceptance_criteria: list[NonemptyText] | None
    mvp: list[NonemptyText] | None


class LegacyAnalysis(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    is_demo: bool = True


class MessageReadBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    requirements_version: int | None = None

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone information from stored timestamps.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class BusinessMessageRead(MessageReadBase):
    mode: Literal[Mode.business_analyst]
    analysis: BusinessAnalysis | None = None


class SystemMessageRead(MessageReadBase):
    mode: Literal[Mode.system_analyst]
    analysis: SystemAnalysis | None = None
    is_stale: bool = False


class ClarificationMessageRead(MessageReadBase):
    mode: Literal[Mode.clarification]
    analysis: None = None


class LegacyMessageRead(MessageReadBase):
    mode: Literal["analyst", "critic", "qa"]
    analysis: LegacyAnalysis | None = None


NewMessageRead = Annotated[
    BusinessMessageRead | SystemMessageRead | ClarificationMessageRead, Field(discriminator="mode")
]
MessageRead = Annotated[
    BusinessMessageRead | SystemMessageRead | ClarificationMessageRead | LegacyMessageRead,
    Field(discriminator="mode"),
]


class ChatTurn(BaseModel):
    user: NewMessageRead
    assistant: NewMessageRead
    requirements: RequirementsRead | None
    requires_edit_clarification: bool = False


class ConversationRead(BaseModel):
    messages: list[MessageRead]
    requirements: RequirementsRead | None
