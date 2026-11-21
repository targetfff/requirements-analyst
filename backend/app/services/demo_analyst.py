"""Hard-coded demo fixture for UI development.

It DOES NOT parse user requests or pretend to be an LLM. Replace with
an actual LangChain/Gemini adapter in a later iteration.
"""

from app.schemas.chat import AnalysisResult, Issue, Mode, Requirement


def build_demo_answer(mode: Mode) -> tuple[str, AnalysisResult]:
    if mode == Mode.critic:
        return (
            "Демонстрация проверки требований. Результат шаблонный и не зависит от текста запроса.",
            AnalysisResult(
                issues=[
                    Issue(severity="high", text="Не уточнены права доступа к документам."),
                    Issue(severity="medium", text="Не описан порядок отклонения и повторной отправки."),
                    Issue(severity="low", text="Не указан срок хранения истории изменений."),
                ],
                questions=[
                    "Кто имеет право просматривать документы?",
                    "Можно ли повторно отправить отклонённый документ?",
                ],
            ),
        )
    if mode == Mode.qa:
        return (
            "Демонстрация критериев приёмки. Результат шаблонный и не зависит от текста запроса.",
            AnalysisResult(
                acceptance_criteria=[
                    "Сотрудник может загрузить документ, и он появляется в списке со статусом «На проверке».",
                    "Руководитель может одобрить или отклонить документ с комментарием.",
                    "Сотрудник без роли руководителя не может одобрять документы.",
                ],
                questions=["Какие форматы и максимальный размер файлов нужно поддерживать?"],
            ),
        )
    return (
        "Демонстрация анализа требований. Результат шаблонный и не зависит от текста запроса.",
        AnalysisResult(
            requirements=[
                Requirement(id="FR-01", text="Сотрудник может загружать документы в систему."),
                Requirement(id="FR-02", text="Руководитель может просматривать, одобрять или отклонять документы."),
                Requirement(id="FR-03", text="Права доступа зависят от роли пользователя."),
            ],
            questions=[
                "Какие форматы файлов необходимо поддерживать?",
                "Нужно ли сохранять историю согласования документов?",
            ],
        ),
    )
