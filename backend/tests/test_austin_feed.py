"""Tests for the Austin traffic camera feed: status filtering, bbox
sanity-check, and entity mapping against the live ontology. The Socrata
HTTP call is mocked via httpx.MockTransport — no network access needed.
Idempotency/ontology tests require Neo4j running (see docker-compose.yml),
like the other integration tests in this suite.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.austin import _cameras_to_entities, poll_cameras
from app.ontology.validate import validate_entity

AUSTIN_DOWNTOWN = [-97.7431, 30.2672]  # [lon, lat], real Austin coords
OUTSIDE_AUSTIN = [-122.4194, 37.7749]  # San Francisco


def _camera_row(camera_id: str, status: str, coords: list[float], **overrides) -> dict:
    row = {
        "camera_id": camera_id,
        "location_name": "830 BLK W RUNDBERG LN",
        "camera_status": status,
        "screenshot_address": f"https://cctv.austinmobility.io/image/{camera_id}.jpg",
        "location": {"type": "Point", "coordinates": coords},
    }
    row.update(overrides)
    return row


def _mock_client(rows: list[dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=rows)

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


def test_cameras_to_entities_drops_non_turned_on_status():
    rows = [_camera_row("1", "DESIRED", AUSTIN_DOWNTOWN)]
    assert _cameras_to_entities(rows) == []


def test_cameras_to_entities_drops_out_of_austin_row():
    rows = [_camera_row("1", "TURNED_ON", OUTSIDE_AUSTIN)]
    assert _cameras_to_entities(rows) == []


def test_cameras_to_entities_drops_row_missing_photo_url():
    rows = [_camera_row("1", "TURNED_ON", AUSTIN_DOWNTOWN, screenshot_address=None)]
    assert _cameras_to_entities(rows) == []


def test_cameras_to_entities_drops_row_with_untrusted_photo_host():
    rows = [_camera_row("1", "TURNED_ON", AUSTIN_DOWNTOWN, screenshot_address="https://evil.example/1.jpg")]
    assert _cameras_to_entities(rows) == []


def test_cameras_to_entities_maps_turned_on_row():
    rows = [_camera_row("1", "TURNED_ON", AUSTIN_DOWNTOWN)]
    entities = _cameras_to_entities(rows)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "AUSTIN-CAMERA-1"
    assert entity.entity_class == "EQUIPMENT"
    assert entity.entity_subclass == "EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA"
    assert entity.label == "830 BLK W RUNDBERG LN"
    assert entity.status == "active"
    assert entity.source_ref == "austin"
    assert entity.attrs["lat"] == pytest.approx(30.2672)
    assert entity.attrs["lon"] == pytest.approx(-97.7431)
    assert entity.attrs["photo_url"] == "https://cctv.austinmobility.io/image/1.jpg"


@pytest.mark.asyncio
async def test_camera_entities_validate_against_live_ontology(db_session):
    rows = [_camera_row("VALIDATE", "TURNED_ON", AUSTIN_DOWNTOWN)]
    for entity in _cameras_to_entities(rows):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_cameras_is_idempotent_on_repeated_poll(db_session):
    entity_id = "AUSTIN-CAMERA-IDEMPOTENT"
    try:
        rows = [_camera_row("IDEMPOTENT", "TURNED_ON", AUSTIN_DOWNTOWN)]
        for _ in range(2):
            client = _mock_client(rows)
            try:
                counts = await poll_cameras(db_session, client=client)
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
