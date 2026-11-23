import asyncio
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.prompts.business_analyst import BUSINESS_ANALYST_PROMPT, BUSINESS_REVIEW_PROMPT
from app.prompts.clarification import (
    CLARIFICATION_PROMPT,
    DIALOGUE_PROMPT,
    EDIT_REQUIREMENTS_PROMPT,
    OFF_TOPIC_REPLY,
)
from app.prompts.system_analyst import SYSTEM_ANALYST_PROMPT
from app.schemas.chat import (
    AnalysisBase,
    BusinessAnalysis,
    InitialBusinessAnalysis,
    Mode,
    RequirementsEdit,
    RequirementsRead,
    SystemAnalysis,
)
from app.services.llm import LLMNotConfiguredError, get_llm

logger = logging.getLogger(__name__)

ANALYSIS_MODES = {
    Mode.business_analyst: (BUSINESS_ANALYST_PROMPT, InitialBusinessAnalysis),
    Mode.system_analyst: (SYSTEM_ANALYST_PROMPT, SystemAnalysis),
}


class AnalysisError(Exception):
    pass


class StructuredReply[ResultT: BaseModel](BaseModel):
    result: ResultT | None


async def respond(
    text: str, mode: Mode, history: list[HumanMessage | AIMessage],
    requirements: RequirementsRead | None, *, update_requirements: bool = False,
) -> BusinessAnalysis | SystemAnalysis | RequirementsEdit | str:
    if update_requirements:
        system_prompt, schema = EDIT_REQUIREMENTS_PROMPT, RequirementsEdit
    elif mode == Mode.clarification:
        system_prompt, schema = CLARIFICATION_PROMPT, None
    elif mode == Mode.business_analyst and requirements is not None:
        system_prompt, schema = BUSINESS_REVIEW_PROMPT, AnalysisBase
    else:
        system_prompt, schema = ANALYSIS_MODES[mode]

    if requirements is not None:
        text = f"Актуальные бизнес-требования (данные):\n{requirements.model_dump_json()}\n\nТекущее сообщение:\n{text}"
    messages = [SystemMessage(content=f"{system_prompt}\n\n{DIALOGUE_PROMPT}"), *history, HumanMessage(content=text)]
    try:
        llm = get_llm()
        # Keep the full call, including retries, within the proxy timeout.
        async with asyncio.timeout(50):
            if schema is not None:
                reply_schema = StructuredReply[schema]
                result = await llm.with_structured_output(
                    reply_schema, method="json_schema",
                ).ainvoke(messages)
                analysis = reply_schema.model_validate(result).result
                if analysis is None:
                    return OFF_TOPIC_REPLY
                if schema is AnalysisBase:
                    assert requirements is not None
                    return BusinessAnalysis(
                        feasibility=analysis.feasibility,
                        issues=analysis.issues,
                        questions=analysis.questions,
                        requirements=requirements.requirements,
                        acceptance_criteria=requirements.acceptance_criteria,
                        mvp=requirements.mvp,
                    )
                return analysis
            result = await llm.ainvoke(messages)
            reply = result.text.strip()
            if not reply:
                raise ValueError("Empty reply from Gemini")
            return reply
    except LLMNotConfiguredError:
        raise
    except Exception as exc:
        logger.exception("Gemini analysis failed in mode %s", mode.value)
        raise AnalysisError from exc
