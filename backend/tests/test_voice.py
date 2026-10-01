"""Voice integration: platform client (login/retry/relay) and KB tools.
The platform is faked with httpx.MockTransport; Neo4j with a canned session.
"""
import httpx
import pytest

from app.config import Settings
from app.services import voice_tools
from app.services.voice_client import VoiceClient, VoicePlatformError
from app.services.voice_tools import ToolError, run_tool


def _settings(**over) -> Settings:
    base = dict(
        voice_api_url="https://voice.example.com",
        voice_agent_id="agent-1",
        voice_workspace_id="",
    )
    return Settings(**{**base, **over})


def _platform(handler):
    return httpx.MockTransport(handler)


def test_voice_configured_requires_url_and_agent():
    assert _settings().voice_configured
    assert not _settings(voice_agent_id="").voice_configured
    assert not _settings(voice_api_url="").voice_configured


def test_client_refuses_to_build_when_unconfigured():
    with pytest.raises(VoicePlatformError) as e:
        VoiceClient(_settings(voice_agent_id=""))
    assert e.value.status_code == 503


async def test_session_relays_sdp_as_the_signed_in_user():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/realtime/session/agent-1"
        assert req.headers["authorization"] == "Bearer user-tok"
        assert req.headers["content-type"] == "application/sdp"
        assert req.content == b"OFFER"
        return httpx.Response(200, json={"sdp": "ANSWER", "tools": [], "agent": {"name": "A"}})

    client = VoiceClient(_settings(), transport=_platform(handler))
    assert (await client.create_webrtc_session("user-tok", "OFFER"))["sdp"] == "ANSWER"


async def test_workspace_id_is_forwarded_when_set():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["workspace_id"] == "ws-1"
        return httpx.Response(200, json={"sdp": "ok"})

    client = VoiceClient(_settings(voice_workspace_id="ws-1"), transport=_platform(handler))
    await client.create_webrtc_session("t", "x")


async def test_platform_errors_become_voice_errors():
    def expired(req):
        return httpx.Response(401, json={"detail": "expired"})

    with pytest.raises(VoicePlatformError) as e:
        await VoiceClient(_settings(), transport=_platform(expired)).create_webrtc_session("t", "x")
    assert e.value.status_code == 401

    def not_premium(req):
        return httpx.Response(400, json={"detail": "only Premium tier"})

    with pytest.raises(VoicePlatformError, match="Premium"):
        await VoiceClient(_settings(), transport=_platform(not_premium)).create_webrtc_session("t", "x")


# ---- tools -----------------------------------------------------------------


async def test_unknown_tool_and_bad_args_raise_tool_error():
    with pytest.raises(ToolError, match="Unknown tool"):
        await run_tool(None, "drop_database", {})
    with pytest.raises(ToolError, match="Invalid arguments"):
        await run_tool(None, "search_knowledge_graph", {"query": ""})
    with pytest.raises(ToolError, match="Invalid arguments"):
        await run_tool(None, "build_scenario", {"entity_id": "x", "hops": 99})


async def test_every_advertised_tool_has_a_handler():
    names = {t["name"] for t in voice_tools.TOOL_DEFINITIONS}
    assert names == set(voice_tools._HANDLERS)


async def test_search_returns_brief_entities(monkeypatch):
    async def fake_search(session, q, limit):
        assert (q, limit) == ("petrov", 8)
        return [{"entity_id": "P-1", "label": "Ivan Petrov", "entity_class": "PERSON",
                 "entity_subclass": "PERSON.MILITARY_PERSONNEL", "status": "active",
                 "aliases": ["I. Petrov"], "attrs": "{}"}]

    monkeypatch.setattr(voice_tools, "search_entities_by_name", fake_search)
    out = await run_tool(None, "search_knowledge_graph", {"query": "petrov"})
    assert out["output"]["entities"][0] == {
        "entity_id": "P-1", "label": "Ivan Petrov", "class": "PERSON.MILITARY_PERSONNEL",
        "status": "active", "aliases": ["I. Petrov"],
    }


class _Rec:
    def __init__(self, found):
        self.found = found

    async def single(self):
        return {"e": {}} if self.found else None


class _Session:
    def __init__(self, found=True):
        self.found = found

    async def run(self, *a, **k):
        return _Rec(self.found)


async def test_scenario_unknown_entity_is_reported():
    with pytest.raises(ToolError, match="No entity"):
        await run_tool(_Session(found=False), "build_scenario", {"entity_id": "nope"})


async def test_scenario_uses_graph_only_and_annotates(monkeypatch):
    nodes = [
        {"entity_id": "A", "label": "Alpha", "entity_class": "UNIT", "attrs": "{}"},
        {"entity_id": "B", "label": "Bravo", "entity_class": "PERSON", "attrs": "{}"},
    ]
    edges = [{"source_entity": "A", "target_entity": "B", "link_type": "COMMANDS", "attrs": "{}"}]

    async def fake_scope(session, eid, hops, link_types=None):
        assert (eid, hops) == ("A", 2)
        return {"nodes": nodes, "edges": edges}

    async def fake_explain(eid, sub):
        return {"annotations": [{"entity_id": "B", "relevance": "high", "rationale": "commands"}]}

    monkeypatch.setattr(voice_tools, "scope_subgraph", fake_scope)
    monkeypatch.setattr(voice_tools, "explain_scope", fake_explain)
    out = await run_tool(_Session(), "build_scenario", {"entity_id": "A"})
    assert out["output"]["related"][0]["relevance"] == "high"
    assert out["output"]["links"] == [{"from": "Alpha", "type": "COMMANDS", "to": "Bravo"}]
    assert out["ui"] == {"type": "scenario", "trigger_entity_id": "A", "hops": 2}


async def test_ingestion_only_proposes_and_hides_batch_from_model(monkeypatch):
    batch = {"batch_id": "B-1", "status": "proposed", "source_text": "t",
             "entities": [{"label": "Alpha"}], "links": [], "rejected_entities": [],
             "rejected_links": []}

    async def fake_extract(session, text):
        return batch

    monkeypatch.setattr(voice_tools, "extract_from_text", fake_extract)
    out = await run_tool(None, "propose_ingestion", {"text": "Alpha exists."})
    assert out["output"]["status"] == "proposed_for_human_review"
    assert "batch" not in out["output"]
    assert out["ui"]["batch"] is batch


# ---- HTTP layer ---------------------------------------------------------------

import httpx as _httpx  # noqa: E402

from app.api import voice as voice_api  # noqa: E402
from app.main import create_app  # noqa: E402


class _Ctx:
    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, *exc):
        return False


class _Driver:
    def session(self):
        return _Ctx()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(voice_api, "get_driver", lambda: _Driver())

    async def fake_instructions(session):
        return "GROUNDED"

    monkeypatch.setattr(voice_api, "build_instructions", fake_instructions)
    monkeypatch.setattr(voice_api, "get_settings", lambda: _settings())
    transport = _httpx.ASGITransport(app=create_app())
    return _httpx.AsyncClient(transport=transport, base_url="http://t")


AUTH = {"Authorization": "Bearer user-tok"}


async def test_session_endpoint_relays_with_user_token_and_swaps_in_kb_tools(api, monkeypatch):
    class Fake:
        def __init__(self, settings):
            pass

        async def create_webrtc_session(self, token, sdp):
            assert (token, sdp) == ("user-tok", "OFFER")
            return {"sdp": "ANSWER", "tools": [{"name": "platform_tool"}], "agent": {"name": "A"}}

    monkeypatch.setattr(voice_api, "VoiceClient", Fake)
    res = await api.post(
        "/voice/session", content="OFFER", headers={**AUTH, "content-type": "application/sdp"}
    )
    body = res.json()
    assert res.status_code == 200
    assert body["sdp"] == "ANSWER" and body["instructions"] == "GROUNDED"
    assert {t["name"] for t in body["tools"]} == {t["name"] for t in voice_tools.TOOL_DEFINITIONS}


async def test_session_endpoint_needs_a_token_and_a_body(api, monkeypatch):
    assert (await api.post("/voice/session", content="x")).status_code == 401
    assert (await api.post("/voice/session", content="", headers=AUTH)).status_code == 400


async def test_session_endpoint_reports_unconfigured_platform(api, monkeypatch):
    monkeypatch.setattr(voice_api, "get_settings", lambda: _settings(voice_agent_id=""))
    assert (await api.post("/voice/session", content="x", headers=AUTH)).status_code == 503


async def test_tool_endpoint_returns_tool_errors_to_the_model(api):
    res = await api.post("/voice/tools/nope", json={"arguments": {}})
    assert res.status_code == 200
    assert "Unknown tool" in res.json()["output"]["error"]
