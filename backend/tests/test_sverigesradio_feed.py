"""Tests for the Sveriges Radio news-episodes feed: .NET date parsing,
entity mapping against the live ontology, and MERGE idempotency. The SR
HTTP call is mocked via httpx.MockTransport — no network access needed.
Requires Neo4j running.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.sverigesradio import (
    _episodes_to_entities,
    _parse_dotnet_date_ms,
    poll_radio_news,
)
from app.ontology.validate import validate_entity


def _episodes_response(episode_id: int) -> dict:
    return {
        "episodes": [
            {
                "id": episode_id,
                "title": "Ekot 17:45",
                "description": "Ekots dagliga sändning.",
                "url": f"https://www.sverigesradio.se/avsnitt/{episode_id}",
                "program": {"id": 4540, "name": "Ekot nyhetssändning"},
                "publishdateutc": "/Date(1788375600000)/",
                "imageurl": "https://static-cdn.sr.se/images/4540/x.jpg",
            }
        ]
    }


def _mock_client(response_json: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db_session():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
    await driver.close()


def test_parse_dotnet_date_ms():
    assert _parse_dotnet_date_ms("/Date(1788375600000)/") == 1788375600000
    assert _parse_dotnet_date_ms(None) is None
    assert _parse_dotnet_date_ms("not a date") is None


def test_episodes_to_entities_maps_row():
    payload = _episodes_response(2848774)
    entities = _episodes_to_entities(payload["episodes"])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "SR-NEWS-2848774"
    assert entity.entity_class == "INFORMATION_OBJECT"
    assert entity.entity_subclass == "INFORMATION_OBJECT.OPEN_SOURCE_ITEM.BROADCAST_SEGMENT"
    assert entity.label == "Ekot 17:45"
    assert entity.attrs["program_name"] == "Ekot nyhetssändning"
    assert entity.attrs["published_at_ms"] == 1788375600000


def test_episodes_to_entities_skips_row_without_id():
    payload = _episodes_response(2848774)
    payload["episodes"][0]["id"] = None
    assert _episodes_to_entities(payload["episodes"]) == []


@pytest.mark.asyncio
async def test_radio_news_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check:
    INFORMATION_OBJECT.OPEN_SOURCE_ITEM.BROADCAST_SEGMENT must actually
    exist as a ClassDef leaf."""
    episodes = _episodes_response(2848775)["episodes"]
    for entity in _episodes_to_entities(episodes):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_radio_news_is_idempotent_on_repeated_poll(db_session):
    entity_id = "SR-NEWS-2848776"
    try:
        response = _episodes_response(2848776)
        for _ in range(2):
            client = _mock_client(response)
            try:
                counts = await poll_radio_news(db_session, client=client)
            finally:
                await client.aclose()
            assert counts == {"fetched": 1, "written": 1}

        result = await db_session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN count(e) AS c", id=entity_id
        )
        record = await result.single()
        assert record["c"] == 1
    finally:
        await db_session.run("MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id)
