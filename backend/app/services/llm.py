from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings


class LLMNotConfiguredError(Exception):
    pass


@lru_cache(maxsize=1)
def get_llm() -> ChatGoogleGenerativeAI:
    if not settings.google_api_key:
        raise LLMNotConfiguredError("GOOGLE_API_KEY is required to use Gemini")

    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.google_api_key,
        vertexai=False,
        temperature=None,
        request_timeout=30,
        retries=2,
    )
