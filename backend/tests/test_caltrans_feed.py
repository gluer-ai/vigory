"""Tests for the Caltrans CCTV feed: per-district fan-out, in-service
filtering, official-host image pinning, and entity mapping against the live
ontology. The Caltrans HTTP calls are mocked via httpx.MockTransport — no
network access needed. Idempotency/ontology tests require Neo4j running
(see docker-compose.yml), like the other integration tests in this suite.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.caltrans import _cameras_to_entities, _parse_districts, poll_cameras
from app.ontology.validate import validate_entity


def _cctv_row(index: str, location_name: str, lat: str, lon: str, **overrides) -> dict:
    cctv = {
        "index": index,
        "location": {
            "district": "4",
            "locationName": location_name,
            "nearbyPlace": "Oakland",
            "latitude": lat,
            "longitude": lon,
            "direction": "West",
        },
        "inService": "true",
        "imageData": {
            "static": {
                "currentImageURL": f"https://cwwp2.dot.ca.gov/data/d4/cctv/image/{index}/{index}.jpg",
            }
        },
    }
    cctv.update(overrides)
    return {"cctv": cctv}


def _mock_client_by_district(responses: dict[int, dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for district, payload in responses.items():
            if f"cctvStatusD{district:02d}.json" in str(request.url):
                return httpx.Response(200, json=payload)
        raise AssertionError(f"no mocked response for request: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _mock_client_with_failing_district(ok_district: int, ok_payload: dict, failing_district: int) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if f"cctvStatusD{failing_district:02d}.json" in str(request.url):
            return httpx.Response(503, text="upstream down")
        if f"cctvStatusD{ok_district:02d}.json" in str(request.url):
            return httpx.Response(200, json=ok_payload)
        raise AssertionError(f"no mocked response for request: {request.url}")

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


def test_parse_districts_keeps_only_valid_1_to_12():
    assert _parse_districts("4,7,11,3") == [4, 7, 11, 3]
    assert _parse_districts("0,13,abc,5") == [5]
    assert _parse_districts("") == []


def test_cameras_to_entities_drops_out_of_service_row():
    rows = [(4, _cctv_row("tv102", "TV102 -- I-580 : West of SR-24", "37.82539", "-122.27291", inService="false")["cctv"])]
    assert _cameras_to_entities(rows) == []


def test_cameras_to_entities_drops_row_with_untrusted_photo_host():
    row = _cctv_row("tv102", "TV102 -- I-580 : West of SR-24", "37.82539", "-122.27291")["cctv"]
    row["imageData"]["static"]["currentImageURL"] = "https://evil.example/tv102.jpg"
    assert _cameras_to_entities([(4, row)]) == []


def test_cameras_to_entities_maps_in_service_row():
    row = _cctv_row("tv102", "TV102 -- I-580 : West of SR-24", "37.82539", "-122.27291")["cctv"]
    entities = _cameras_to_entities([(4, row)])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "CALTRANS-CAMERA-D4-tv102"
    assert entity.entity_class == "EQUIPMENT"
    assert entity.entity_subclass == "EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA"
    assert entity.label == "I-580 : West of SR-24 (Oakland)"
    assert entity.source_ref == "caltrans"
    assert entity.attrs["lat"] == pytest.approx(37.82539)
    assert entity.attrs["lon"] == pytest.approx(-122.27291)
    assert entity.attrs["photo_url"] == "https://cwwp2.dot.ca.gov/data/d4/cctv/image/tv102/tv102.jpg"


@pytest.mark.asyncio
async def test_fetch_rows_ignores_a_failing_district():
    payload_ok = {"data": [_cctv_row("tv102", "TV102 -- I-580 : West of SR-24", "37.82539", "-122.27291")]}
    from app.feeds.caltrans import _fetch_rows

    client = _mock_client_with_failing_district(ok_district=4, ok_payload=payload_ok, failing_district=7)
    try:
        rows = await _fetch_rows([4, 7], client=client)
    finally:
        await client.aclose()
    assert len(rows) == 1
    assert rows[0][0] == 4


@pytest.mark.asyncio
async def test_camera_entities_validate_against_live_ontology(db_session):
    row = _cctv_row("validate", "VALIDATE -- Test Rd", "37.82539", "-122.27291")["cctv"]
    for entity in _cameras_to_entities([(4, row)]):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_cameras_is_idempotent_on_repeated_poll(db_session):
    entity_id = "CALTRANS-CAMERA-D4-idempotent"
    try:
        payload = {"data": [_cctv_row("idempotent", "IDEMPOTENT -- Test Rd", "37.82539", "-122.27291")]}
        for _ in range(2):
            client = _mock_client_by_district({4: payload, 7: {"data": []}, 11: {"data": []}, 3: {"data": []}})
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
