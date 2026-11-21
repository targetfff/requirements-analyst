from app.models.conversation import RequirementsVersion
from app.schemas.chat import (
    BusinessAnalysis,
    Requirement,
    RequirementsDocument,
    RequirementsEdit,
    RequirementsRead,
)

MAX_REQUIREMENTS_CHARS = 16_000


class InvalidRequirementsEdit(ValueError):
    pass


def read_requirements(version: RequirementsVersion | None) -> RequirementsRead | None:
    if version is None:
        return None
    return RequirementsRead(version=version.version, **version.document)


def _make_version(
    version: int, document: RequirementsDocument, next_number: int,
) -> RequirementsVersion:
    if len(document.model_dump_json()) > MAX_REQUIREMENTS_CHARS:
        raise InvalidRequirementsEdit(
            "Актуальные требования превысили лимит контекста. Сократите описание."
        )
    return RequirementsVersion(
        version=version,
        document=document.model_dump(mode="json"),
        next_requirement_number=next_number,
    )


def initial_requirements(analysis: BusinessAnalysis) -> RequirementsVersion | None:
    if not analysis.requirements:
        return None
    document = RequirementsDocument(**analysis.model_dump())
    return _make_version(1, document, len(document.requirements) + 1)


def revise_requirements(
    current: RequirementsVersion, edit: RequirementsEdit,
) -> RequirementsVersion | None:
    if edit.decision != "apply":
        return None

    original = RequirementsDocument.model_validate(current.document)
    requirements = {requirement.id: requirement for requirement in original.requirements}
    original_ids = set(requirements)
    next_number = current.next_requirement_number
    edited_ids = set()
    for change in edit.changes:
        if change.operation == "add":
            if change.id is not None or change.text is None:
                raise InvalidRequirementsEdit("Некорректное добавление требования")
            requirement_id = f"FR-{next_number:02d}"
            requirements[requirement_id] = Requirement(id=requirement_id, text=change.text)
            next_number += 1
            continue

        requirement_id = change.id
        if requirement_id is None or requirement_id not in original_ids or requirement_id in edited_ids:
            raise InvalidRequirementsEdit("Неоднозначная ссылка на требование в ответе Gemini")
        edited_ids.add(requirement_id)
        if change.operation == "remove" and change.text is None:
            del requirements[requirement_id]
        elif change.operation == "replace" and change.text is not None:
            requirements[requirement_id] = Requirement(id=requirement_id, text=change.text)
        else:
            raise InvalidRequirementsEdit("Некорректное изменение требования")

    document = RequirementsDocument(
        requirements=list(requirements.values()),
        acceptance_criteria=(
            original.acceptance_criteria if edit.acceptance_criteria is None
            else edit.acceptance_criteria
        ),
        mvp=original.mvp if edit.mvp is None else edit.mvp,
    )
    if document == original:
        return None
    return _make_version(current.version + 1, document, next_number)
