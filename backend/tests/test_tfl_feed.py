"""Tests for the TfL JamCam feed: availability filtering, official-host
image pinning, and entity mapping against the live ontology. The TfL HTTP
call is mocked via httpx.MockTransport — no network access or key needed.
Idempotency/ontology tests require Neo4j running (see docker-compose.yml),
like the other integration tests in this suite.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.tfl import _cameras_to_entities, poll_cameras
from app.ontology.validate import validate_entity

LONDON = (51.60067, -0.01594)


def _place(place_id: str, name: str, lat: float, lon: float, *, available: str = "true", image_url: str | None = None) -> dict:
    return {
        "id": place_id,
        "commonName": name,
        "lat": lat,
        "lon": lon,
        "additionalProperties": [
            {"key": "available", "value": available},
            {
                "key": "imageUrl",
                "value": image_url
                or f"https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/{place_id.removeprefix('JamCams_')}.jpg",
            },
        ],
    }


def _mock_client(places: list[dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=places)

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


def test_cameras_to_entities_drops_unavailable_camera():
    places = [_place("JamCams_00002.00865", "A406 Billet Upass E", *LONDON, available="false")]
    assert _cameras_to_entities(places) == []


def test_cameras_to_entities_drops_untrusted_photo_host():
    places = [
        _place(
            "JamCams_00002.00865",
            "A406 Billet Upass E",
            *LONDON,
            image_url="https://evil.example/00002.00865.jpg",
        )
    ]
    assert _cameras_to_entities(places) == []


def test_cameras_to_entities_maps_available_camera():
    places = [_place("JamCams_00002.00865", "A406 Billet Upass E", *LONDON)]
    entities = _cameras_to_entities(places)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "TFL-CAMERA-00002.00865"
    assert entity.entity_class == "EQUIPMENT"
    assert entity.entity_subclass == "EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA"
    assert entity.label == "A406 Billet Upass E"
    assert entity.source_ref == "tfl"
    assert entity.attrs["lat"] == pytest.approx(51.60067)
    assert entity.attrs["lon"] == pytest.approx(-0.01594)
    assert entity.attrs["photo_url"] == "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00002.00865.jpg"


@pytest.mark.asyncio
async def test_camera_entities_validate_against_live_ontology(db_session):
    places = [_place("JamCams_VALIDATE", "Test Camera", *LONDON)]
    for entity in _cameras_to_entities(places):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_cameras_is_idempotent_on_repeated_poll(db_session):
    entity_id = "TFL-CAMERA-IDEMPOTENT"
    try:
        places = [_place("JamCams_IDEMPOTENT", "Test Camera", *LONDON)]
        for _ in range(2):
            client = _mock_client(places)
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
