"""Tests for the OpenSky aircraft feed: bbox filtering, entity mapping
against the live ontology, and MERGE idempotency. The OpenSky HTTP call is
mocked via httpx.MockTransport — no network access needed. Requires Neo4j
running.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.opensky import _states_to_entities, poll_aircraft
from app.ontology.validate import validate_entity

# icao24, callsign, origin_country, time_position, last_contact, longitude,
# latitude, baro_altitude, on_ground, velocity, true_track, vertical_rate,
# sensors, geo_altitude, squawk, spi, position_source, category
STOCKHOLM_ROW = ["abc123", "SAS123  ", "Sweden", 1700000000, 1700000000, 18.06, 59.33,
                  10000.0, False, 230.0, 90.0, 0.0, None, 10500.0, "1000", False, 0, 3]
BERLIN_ROW = ["def456", "DLH456  ", "Germany", 1700000000, 1700000000, 13.4, 52.5,
              9000.0, False, 210.0, 180.0, 0.0, None, 9200.0, "2000", False, 0, 3]


def _states_response(rows: list[list]) -> dict:
    return {"time": 1700000000, "states": rows}


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


def test_states_to_entities_drops_out_of_bbox_row():
    entities = _states_to_entities([BERLIN_ROW])
    assert entities == []


def test_states_to_entities_maps_in_bbox_row():
    entities = _states_to_entities([STOCKHOLM_ROW])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "OPENSKY-abc123"
    assert entity.entity_class == "VEHICLE"
    assert entity.entity_subclass == "VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT"
    assert entity.label == "SAS123"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)
    assert entity.attrs["altitude_m"] == pytest.approx(10000.0)


def test_states_to_entities_filters_bbox_and_keeps_in_bbox_row():
    entities = _states_to_entities([STOCKHOLM_ROW, BERLIN_ROW])
    assert [e.entity_id for e in entities] == ["OPENSKY-abc123"]


@pytest.mark.asyncio
async def test_aircraft_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check: VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT
    must actually exist as a ClassDef key."""
    for entity in _states_to_entities([STOCKHOLM_ROW]):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_aircraft_is_idempotent_on_repeated_poll(db_session):
    entity_id = "OPENSKY-abc123"
    try:
        response = _states_response([STOCKHOLM_ROW])
        for _ in range(2):
            client = _mock_client(response)
            try:
                counts = await poll_aircraft(db_session, client=client)
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
