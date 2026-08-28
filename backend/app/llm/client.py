"""Thin litellm wrapper: swap OpenAI/Anthropic via env, no dual SDK code."""
import json

import litellm

from app.config import get_settings


class LLMError(Exception):
    """Raised on missing credentials or a failed LLM call, so API routes can
    surface a clean 502 instead of an unhandled 500 with a stack trace."""


async def complete_json(system_prompt: str, user_prompt: str) -> dict:
    """Call the configured LLM in JSON mode and return the parsed object."""
    settings = get_settings()
    litellm.openai_key = settings.openai_api_key or None
    litellm.anthropic_key = settings.anthropic_api_key or None

    if settings.llm_provider == "openai" and not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not set (required for LLM_PROVIDER=openai)")
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set (required for LLM_PROVIDER=anthropic)")

    try:
        response = await litellm.acompletion(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    except Exception as e:
        raise LLMError(f"LLM call failed: {e}") from e

    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError("LLM did not return valid JSON") from e
