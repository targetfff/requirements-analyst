import asyncio
import logging

from langchain_core.prompts import ChatPromptTemplate

from app.prompts.system import SYSTEM_PROMPTS
from app.schemas.chat import LLMAnalysis, Mode
from app.services.llm import LLMNotConfiguredError, get_llm

logger = logging.getLogger(__name__)


class AnalysisError(Exception):
    pass


async def analyze(text: str, mode: Mode) -> LLMAnalysis:
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPTS[mode.value]),
        ("human", "{text}"),
    ])
    try:
        chain = prompt | get_llm().with_structured_output(LLMAnalysis, method="json_schema")
        # Keep the full call, including retries, within the proxy timeout.
        async with asyncio.timeout(50):
            result = await chain.ainvoke({"text": text})
        return LLMAnalysis.model_validate(result)
    except LLMNotConfiguredError:
        raise
    except Exception as exc:
        logger.exception("Gemini analysis failed in mode %s", mode.value)
        raise AnalysisError from exc
