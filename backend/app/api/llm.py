"""GET /llm/status — what the configured model is doing right now.

Combines (a) a health probe of the model server (for self-hosted Ollama:
reachable? model downloaded? model loaded in memory?) with (b) the in-flight
calls tracked by app.llm.activity, so the UI can show "Qwen3 is extracting
entities (42s)" rather than a silent spinner.
"""
import httpx
from fastapi import APIRouter

from app.config import get_settings
from app.llm.activity import get_activity
from app.llm.client import is_ollama

router = APIRouter(prefix="/llm", tags=["llm"])

_PROBE_TIMEOUT_SECONDS = 3.0


def _ollama_tag(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


async def _probe_ollama(api_base: str, model: str) -> dict:
    base = (api_base or "http://localhost:11434").rstrip("/")
    tag = _ollama_tag(model)
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            installed = (await client.get(f"{base}/api/tags")).json().get("models", [])
            running = (await client.get(f"{base}/api/ps")).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return {"reachable": False, "model_installed": False, "model_loaded": False}
    return {
        "reachable": True,
        "model_installed": any(m.get("name") == tag for m in installed),
        "model_loaded": any(m.get("name") == tag for m in running),
    }


@router.get("/status")
async def llm_status():
    settings = get_settings()
    local = is_ollama(settings.llm_provider, settings.llm_model)
    activity = get_activity().snapshot()

    if local:
        health = await _probe_ollama(settings.llm_api_base, settings.llm_model)
        if not health["reachable"]:
            state = "unreachable"
        elif not health["model_installed"]:
            state = "downloading"  # Ollama is up but the model pull has not finished
        else:
            state = "ready"
    else:
        health = {"reachable": None, "model_installed": None, "model_loaded": None}
        key = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}.get(
            settings.llm_provider, "configured"
        )
        state = "ready" if key else "unconfigured"

    if state == "ready" and activity["active"]:
        state = "busy"

    return {
        "state": state,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "local": local,
        **health,
        **activity,
    }
