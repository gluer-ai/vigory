"""Thin litellm wrapper: swap OpenAI/Anthropic/Ollama via env, no dual SDK code."""
import json
import logging
import re

import litellm

from app.config import get_settings
from app.llm.activity import current_operation, get_activity


class LLMError(Exception):
    """Raised on missing credentials or a failed LLM call, so API routes can
    surface a clean 502 instead of an unhandled 500 with a stack trace."""


logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def is_ollama(provider: str, model: str) -> bool:
    return provider == "ollama" or model.startswith(("ollama/", "ollama_chat/"))


async def complete_json(system_prompt: str, user_prompt: str, *, light: bool = False) -> dict:
    """Call the configured LLM in JSON mode and return the parsed object.

    light=True marks a short, constrained job (see Settings.light_llm_model):
    it goes to the small model when one is configured, and falls back to the
    main model if the small one fails. Everything else uses the main model."""
    settings = get_settings()
    if light and settings.light_llm_model:
        try:
            return await _complete_json(
                system_prompt,
                user_prompt,
                model=settings.light_llm_model,
                api_base=settings.light_llm_api_base,
                timeout=settings.light_llm_timeout_seconds,
                max_tokens=settings.light_llm_max_tokens,
            )
        except LLMError as e:
            logger.warning("light model %s failed (%s); using %s", settings.light_llm_model, e, settings.llm_model)
    return await _complete_json(
        system_prompt,
        user_prompt,
        model=settings.llm_model,
        api_base=settings.llm_api_base,
        timeout=settings.llm_timeout_seconds,
        max_tokens=None,
    )


async def _complete_json(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    api_base: str,
    timeout: int,
    max_tokens: int | None,
) -> dict:
    settings = get_settings()
    litellm.openai_key = settings.openai_api_key or None
    litellm.anthropic_key = settings.anthropic_api_key or None

    # Credentials are only checked for the provider the MAIN model uses; a light
    # model is a self-hosted one (no key) by construction.
    if model == settings.llm_model:
        if settings.llm_provider == "openai" and not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set (required for LLM_PROVIDER=openai)")
        if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (required for LLM_PROVIDER=anthropic)")

    if settings.llm_disable_thinking and "qwen3" in model.lower():
        system_prompt = f"{system_prompt}\n\n/no_think"

    try:
        label = f"{current_operation()} [{model.split('/')[-1]}]"
        with get_activity().track(label):
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                api_base=api_base or None,
                timeout=timeout,
                **({"max_tokens": max_tokens} if max_tokens else {}),
            )
    except Exception as e:
        raise LLMError(f"LLM call failed: {e}") from e

    # Reasoning models may leave a <think> block in the content.
    content = _THINK_BLOCK.sub("", response.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError("LLM did not return valid JSON") from e
    if not isinstance(parsed, dict):
        raise LLMError("LLM did not return a JSON object")
    return parsed
