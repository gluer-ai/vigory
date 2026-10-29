"""Small-model support: call tracking, <think> stripping, api_base wiring,
GET /llm/status, and the background text-ingest job lifecycle."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.api import llm as llm_api
from app.config import Settings
from app.llm import client as client_module
from app.llm.activity import LLMActivity, llm_operation
from app.main import create_app
from app.services import extraction_agent as extraction_module


def test_activity_tracks_active_and_last_call():
    now = [100.0]
    activity = LLMActivity(clock=lambda: now[0])
    with activity.track("Extracting"):
        now[0] = 112.5
        snap = activity.snapshot()
        assert snap["active"] == [{"operation": "Extracting", "elapsed_seconds": 12.5}]
    snap = activity.snapshot()
    assert snap["active"] == []
    assert snap["last_call"] == {"operation": "Extracting", "duration_seconds": 12.5, "ok": True}


def test_activity_records_failed_call():
    activity = LLMActivity(clock=lambda: 0.0)
    with pytest.raises(RuntimeError):
        with activity.track("x"):
            raise RuntimeError("boom")
    assert activity.snapshot()["last_call"]["ok"] is False


def test_llm_operation_nests_labels():
    from app.llm.activity import current_operation

    assert current_operation() == "LLM call"
    with llm_operation("a.pdf: part 1/3"):
        with llm_operation("Extracting"):
            assert current_operation() == "a.pdf: part 1/3: Extracting"
    assert current_operation() == "LLM call"


def _fake_litellm(monkeypatch, content, captured):
    async def acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(client_module.litellm, "acompletion", acompletion)


def _settings(monkeypatch, **overrides):
    s = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(client_module, "get_settings", lambda: s)
    return s


@pytest.mark.asyncio
async def test_complete_json_ollama_strips_think_and_passes_api_base(monkeypatch):
    _settings(
        monkeypatch,
        llm_provider="ollama",
        llm_model="ollama_chat/qwen3:8b",
        llm_api_base="http://ollama.railway.internal:11434",
    )
    captured: dict = {}
    _fake_litellm(monkeypatch, '<think>hmm</think>\n{"ok": true}', captured)

    assert await client_module.complete_json("sys", "user") == {"ok": True}
    assert captured["api_base"] == "http://ollama.railway.internal:11434"
    assert captured["timeout"] == 600
    assert captured["messages"][0]["content"].endswith("/no_think")


@pytest.mark.asyncio
async def test_complete_json_does_not_add_no_think_for_other_models(monkeypatch):
    _settings(monkeypatch, llm_provider="openai", llm_model="gpt-4o", openai_api_key="k")
    captured: dict = {}
    _fake_litellm(monkeypatch, '{"ok": 1}', captured)
    await client_module.complete_json("sys", "user")
    assert captured["messages"][0]["content"] == "sys"
    assert captured["api_base"] is None


@pytest.mark.asyncio
async def test_complete_json_rejects_non_object(monkeypatch):
    _settings(monkeypatch, llm_provider="openai", llm_model="gpt-4o", openai_api_key="k")
    _fake_litellm(monkeypatch, "[1, 2]", {})
    with pytest.raises(client_module.LLMError):
        await client_module.complete_json("s", "u")


async def _get_status(monkeypatch, settings, probe=None):
    monkeypatch.setattr(llm_api, "get_settings", lambda: settings)
    if probe is not None:

        async def fake_probe(api_base, model):
            return probe

        monkeypatch.setattr(llm_api, "_probe_ollama", fake_probe)
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        res = await c.get("/llm/status")
    assert res.status_code == 200
    return res.json()


@pytest.mark.asyncio
async def test_status_states_for_ollama(monkeypatch):
    s = Settings(_env_file=None, llm_provider="ollama", llm_model="ollama_chat/qwen3:8b")
    up = {"reachable": True, "model_installed": True, "model_loaded": False}
    assert (await _get_status(monkeypatch, s, up))["state"] == "ready"
    down = {"reachable": False, "model_installed": False, "model_loaded": False}
    assert (await _get_status(monkeypatch, s, down))["state"] == "unreachable"
    pulling = {"reachable": True, "model_installed": False, "model_loaded": False}
    body = await _get_status(monkeypatch, s, pulling)
    assert body["state"] == "downloading" and body["local"] is True


@pytest.mark.asyncio
async def test_status_busy_while_call_in_flight(monkeypatch):
    from app.llm.activity import get_activity

    s = Settings(_env_file=None, llm_provider="ollama", llm_model="ollama_chat/qwen3:8b")
    up = {"reachable": True, "model_installed": True, "model_loaded": True}
    with get_activity().track("Extracting entities and links"):
        body = await _get_status(monkeypatch, s, up)
    assert body["state"] == "busy"
    assert body["active"][0]["operation"] == "Extracting entities and links"


@pytest.mark.asyncio
async def test_status_hosted_provider_without_key_is_unconfigured(monkeypatch):
    s = Settings(_env_file=None, llm_provider="openai", llm_model="gpt-4o", openai_api_key="")
    body = await _get_status(monkeypatch, s)
    assert body["state"] == "unconfigured" and body["local"] is False


class _Record:
    def __init__(self, data):
        self._d = data

    def __getitem__(self, k):
        return self._d[k]


class _Result:
    def __init__(self, record=None):
        self._r = record

    async def single(self):
        return self._r


class _Session:
    def __init__(self):
        self.calls = []

    async def run(self, query, **params):
        self.calls.append((query, params))
        if "RETURN b.source_text" in query:
            return _Result(_Record({"text": "some scenario"}))
        return _Result()


class _Ctx:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


def _patch_driver(monkeypatch, session):
    import app.db.neo4j_client as neo

    monkeypatch.setattr(neo, "get_driver", lambda: SimpleNamespace(session=lambda: _Ctx(session)))


@pytest.mark.asyncio
async def test_process_text_batch_marks_proposed_and_reports_progress(monkeypatch):
    session = _Session()
    _patch_driver(monkeypatch, session)

    async def fake_extract(sess, text, extra_existing_entities=None, progress=None):
        await progress("Extracting entities and links", None, None)
        return {
            "valid_entities": [{"entity_id": "E1"}],
            "valid_links": [],
            "rejected_entities": [],
            "rejected_links": [],
        }

    monkeypatch.setattr(extraction_module, "_extract_and_validate", fake_extract)
    await extraction_module.process_text_batch("B-1")

    queries = [q for q, _ in session.calls]
    assert any("SET n.stage" in q for q in queries)
    final_q, final_p = session.calls[-1]
    assert "b.status = 'proposed'" in final_q
    assert json.loads(final_p["entities"]) == [{"entity_id": "E1"}]


@pytest.mark.asyncio
async def test_process_text_batch_records_error(monkeypatch):
    session = _Session()
    _patch_driver(monkeypatch, session)

    async def boom(*a, **k):
        raise extraction_module.LLMError("LLM call failed: timeout")

    monkeypatch.setattr(extraction_module, "_extract_and_validate", boom)
    await extraction_module.process_text_batch("B-1")

    final_q, final_p = session.calls[-1]
    assert "b.status = 'error'" in final_q
    assert "timeout" in final_p["msg"]


def _routing_litellm(monkeypatch, outcomes):
    """outcomes: model -> content string, or an Exception to raise."""
    calls = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[kwargs["model"]]
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])

    monkeypatch.setattr(client_module.litellm, "acompletion", acompletion)
    return calls


LIGHT = dict(
    llm_provider="openai",
    llm_model="gpt-4o",
    openai_api_key="k",
    light_llm_model="ollama_chat/qwen3:4b",
    light_llm_api_base="http://ollama.railway.internal:11434",
)


@pytest.mark.asyncio
async def test_light_job_uses_small_model_with_its_own_limits(monkeypatch):
    _settings(monkeypatch, **LIGHT)
    calls = _routing_litellm(monkeypatch, {"ollama_chat/qwen3:4b": '{"match_entity_id": null}'})

    assert await client_module.complete_json("s", "u", light=True) == {"match_entity_id": None}
    assert [c["model"] for c in calls] == ["ollama_chat/qwen3:4b"]
    assert calls[0]["api_base"] == "http://ollama.railway.internal:11434"
    assert calls[0]["timeout"] == 90
    assert calls[0]["max_tokens"] == 800


@pytest.mark.asyncio
async def test_light_job_falls_back_to_main_model_on_failure(monkeypatch):
    _settings(monkeypatch, **LIGHT)
    calls = _routing_litellm(
        monkeypatch,
        {"ollama_chat/qwen3:4b": TimeoutError("slow"), "gpt-4o": '{"from": "main"}'},
    )

    assert await client_module.complete_json("s", "u", light=True) == {"from": "main"}
    assert [c["model"] for c in calls] == ["ollama_chat/qwen3:4b", "gpt-4o"]
    assert "max_tokens" not in calls[1]


@pytest.mark.asyncio
async def test_light_job_falls_back_on_invalid_json(monkeypatch):
    _settings(monkeypatch, **LIGHT)
    calls = _routing_litellm(monkeypatch, {"ollama_chat/qwen3:4b": '{"cut off', "gpt-4o": '{"ok": 1}'})

    assert await client_module.complete_json("s", "u", light=True) == {"ok": 1}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_heavy_job_never_uses_light_model(monkeypatch):
    _settings(monkeypatch, **LIGHT)
    calls = _routing_litellm(monkeypatch, {"gpt-4o": '{"ok": 1}'})

    await client_module.complete_json("s", "u")
    assert [c["model"] for c in calls] == ["gpt-4o"]


@pytest.mark.asyncio
async def test_light_flag_without_light_model_uses_main(monkeypatch):
    _settings(monkeypatch, llm_provider="openai", llm_model="gpt-4o", openai_api_key="k")
    calls = _routing_litellm(monkeypatch, {"gpt-4o": '{"ok": 1}'})

    await client_module.complete_json("s", "u", light=True)
    assert [c["model"] for c in calls] == ["gpt-4o"]


@pytest.mark.asyncio
async def test_activity_label_names_the_model(monkeypatch):
    from app.llm.activity import get_activity

    _settings(monkeypatch, **LIGHT)
    seen = []

    async def acompletion(**kwargs):
        seen.append(get_activity().snapshot()["active"][0]["operation"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    monkeypatch.setattr(client_module.litellm, "acompletion", acompletion)
    with llm_operation("Checking for a duplicate entity"):
        await client_module.complete_json("s", "u", light=True)
    assert seen == ["Checking for a duplicate entity [qwen3:4b]"]
