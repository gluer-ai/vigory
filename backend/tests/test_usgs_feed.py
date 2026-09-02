"""Tests for the USGS earthquakes feed: entity mapping against the live
ontology and MERGE idempotency. The USGS HTTP call is mocked via
httpx.MockTransport — no network access needed. Requires Neo4j running.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.usgs import _features_to_entities, poll_earthquakes
from app.ontology.validate import validate_entity


def _geojson_response(feature_id: str) -> dict:
    return {
        "features": [
            {
                "id": feature_id,
                "properties": {
                    "mag": 4.2,
                    "place": "10km NE of Testville",
                    "time": 1700000000000,
                    "magType": "mb",
                    "tsunami": 0,
                    "alert": None,
                    "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{feature_id}",
                },
                "geometry": {"type": "Point", "coordinates": [18.06, 59.33, 10.0]},
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


def test_features_to_entities_maps_row():
    payload = _geojson_response("us7000abcd")
    entities = _features_to_entities(payload["features"])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "USGS-EARTHQUAKE-us7000abcd"
    assert entity.entity_class == "EVENT"
    assert entity.entity_subclass == "EVENT.INCIDENT.NATURAL_DISASTER"
    assert entity.status == "historical"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)
    assert entity.attrs["depth_km"] == pytest.approx(10.0)
    assert entity.attrs["magnitude"] == pytest.approx(4.2)


def test_features_to_entities_skips_row_without_id():
    payload = _geojson_response("us7000abcd")
    payload["features"][0]["id"] = None
    assert _features_to_entities(payload["features"]) == []


@pytest.mark.asyncio
async def test_earthquake_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check: EVENT.INCIDENT.NATURAL_DISASTER
    must actually exist as a ClassDef leaf, and 'historical' must be a valid status."""
    features = _geojson_response("us7000validate")["features"]
    for entity in _features_to_entities(features):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_earthquakes_is_idempotent_on_repeated_poll(db_session):
    entity_id = "USGS-EARTHQUAKE-us7000idempotent"
    try:
        response = _geojson_response("us7000idempotent")
        for _ in range(2):
            client = _mock_client(response)
            try:
                counts = await poll_earthquakes(db_session, client=client)
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
