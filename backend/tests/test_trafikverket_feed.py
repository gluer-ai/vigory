"""Tests for the Trafikverket feed: bbox filtering, entity mapping against
the live ontology, and MERGE idempotency. The Trafikverket HTTP call is
mocked via httpx.MockTransport — no network access and no real API key
needed. Requires Neo4j running (see docker-compose.yml), like the other
integration tests in this suite.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.bbox import in_bbox
from app.feeds.trafikverket import (
    _announcements_to_entities,
    _cameras_to_entities,
    _situations_to_entities,
    _station_coords,
    _stations_to_entities,
    poll_announcements,
    poll_cameras,
    poll_situations,
    poll_stations,
)
from app.ontology.validate import validate_entity

STOCKHOLM = "POINT (18.06 59.33)"  # in Sweden
BERLIN = "POINT (13.4 52.5)"  # outside the Sweden bbox


def _situation_response(deviation_id: str, point: str) -> dict:
    return {
        "RESPONSE": {
            "RESULT": [
                {
                    "Situation": [
                        {
                            "Id": "SE_TEST_1",
                            "Deviation": [
                                {
                                    "Id": deviation_id,
                                    "Header": "Roadworks on E4",
                                    "Message": "Lane closure",
                                    "MessageType": "Roadworks",
                                    "SeverityText": "Low",
                                    "RoadNumber": "E4",
                                    "CountyNo": 1,
                                    "StartTime": "2026-01-01T00:00:00+01:00",
                                    "EndTime": None,
                                    "Geometry": {"WGS84": point},
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }


def _camera_response(camera_id: str, point: str) -> dict:
    return {
        "RESPONSE": {
            "RESULT": [
                {
                    "RoadConditionCamera": [
                        {
                            "Id": camera_id,
                            "Name": "E4 Norr Sodertalje",
                            "CountyNo": 1,
                            "Status": "Active",
                            "PhotoTime": "2026-01-01T00:00:00+01:00",
                            "Geometry": {"WGS84": point},
                        }
                    ]
                }
            ]
        }
    }


def _station_response(signature: str, name: str, point: str) -> dict:
    return {
        "RESPONSE": {
            "RESULT": [
                {
                    "TrainStation": [
                        {
                            "LocationSignature": signature,
                            "AdvertisedLocationName": name,
                            "CountyNo": [1],
                            "Geometry": {"WGS84": point},
                        }
                    ]
                }
            ]
        }
    }


def _announcement_response(activity_id: str, signature: str) -> dict:
    return {
        "RESPONSE": {
            "RESULT": [
                {
                    "TrainAnnouncement": [
                        {
                            "ActivityId": activity_id,
                            "ActivityType": "Avgang",
                            "AdvertisedTrainIdent": "8195",
                            "AdvertisedTimeAtLocation": "2026-09-09T17:48:00.000+02:00",
                            "Canceled": False,
                            "TrackAtLocation": "4",
                            "LocationSignature": signature,
                            "FromLocation": [{"LocationName": "Gä"}],
                            "ToLocation": [{"LocationName": "Blgc"}],
                        }
                    ]
                }
            ]
        }
    }


def _mock_client(response_json: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _mock_client_by_objecttype(responses: dict[str, dict]) -> httpx.AsyncClient:
    """Dispatches to a different canned response depending on the query's
    objecttype attribute — needed for pollers that make more than one
    Trafikverket call per poll (e.g. announcements looking up stations)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        for objecttype, response_json in responses.items():
            if f'objecttype="{objecttype}"' in body:
                return httpx.Response(200, json=response_json)
        raise AssertionError(f"no mocked response for request: {body}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db_session():
    """Own driver per test (not the app-wide singleton) so each test gets a
    driver bound to its own event loop — same pattern as the other
    integration tests in this suite."""
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
    await driver.close()


def test_in_bbox_accepts_sweden_rejects_neighbours():
    assert in_bbox(59.33, 18.06)  # Stockholm
    assert not in_bbox(52.5, 13.4)  # Berlin


def test_situations_to_entities_drops_out_of_sweden_row():
    payload = _situation_response("DEV-1", BERLIN)
    situations = payload["RESPONSE"]["RESULT"][0]["Situation"]
    assert _situations_to_entities(situations) == []


def test_situations_to_entities_maps_sweden_row():
    payload = _situation_response("DEV-1", STOCKHOLM)
    situations = payload["RESPONSE"]["RESULT"][0]["Situation"]
    entities = _situations_to_entities(situations)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "TRAFIKVERKET-SITUATION-DEV-1"
    assert entity.entity_class == "EVENT"
    assert entity.entity_subclass == "EVENT.INCIDENT.TRAFFIC_INCIDENT"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)


def test_cameras_to_entities_drops_out_of_sweden_row():
    payload = _camera_response("CAM-1", BERLIN)
    cameras = payload["RESPONSE"]["RESULT"][0]["RoadConditionCamera"]
    assert _cameras_to_entities(cameras) == []


def test_cameras_to_entities_maps_sweden_row():
    payload = _camera_response("CAM-1", STOCKHOLM)
    cameras = payload["RESPONSE"]["RESULT"][0]["RoadConditionCamera"]
    entities = _cameras_to_entities(cameras)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "TRAFIKVERKET-CAMERA-CAM-1"
    assert entity.entity_class == "EQUIPMENT"
    assert entity.entity_subclass == "EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA"
    assert entity.attrs["photo_url"].endswith("RoadConditionCamera_CAM-1.Jpeg?type=fullsize")


def test_stations_to_entities_drops_out_of_sweden_row():
    payload = _station_response("SLZ", "Salzburg", BERLIN)
    stations = payload["RESPONSE"]["RESULT"][0]["TrainStation"]
    assert _stations_to_entities(stations) == []


def test_stations_to_entities_maps_sweden_row():
    payload = _station_response("A", "Alingsås", STOCKHOLM)
    stations = payload["RESPONSE"]["RESULT"][0]["TrainStation"]
    entities = _stations_to_entities(stations)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "TRAFIKVERKET-STATION-A"
    assert entity.entity_class == "FACILITY"
    assert entity.entity_subclass == "FACILITY.TRANSPORT_NODE"
    assert entity.label == "Alingsås"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)


def test_station_coords_builds_signature_to_latlon_map():
    payload = _station_response("A", "Alingsås", STOCKHOLM)
    stations = payload["RESPONSE"]["RESULT"][0]["TrainStation"]
    coords = _station_coords(stations)
    assert coords == {"A": pytest.approx((59.33, 18.06))}


def test_announcements_to_entities_drops_unknown_station():
    payload = _announcement_response("ACT-1", "UNKNOWN")
    announcements = payload["RESPONSE"]["RESULT"][0]["TrainAnnouncement"]
    assert _announcements_to_entities(announcements, {}) == []


def test_announcements_to_entities_maps_known_station():
    payload = _announcement_response("ACT-1", "A")
    announcements = payload["RESPONSE"]["RESULT"][0]["TrainAnnouncement"]
    entities = _announcements_to_entities(announcements, {"A": (59.33, 18.06)})
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "TRAFIKVERKET-TRAIN-ACT-1"
    assert entity.entity_class == "EVENT"
    assert entity.entity_subclass == "EVENT.MOVEMENT_AND_TRANSIT.RAIL_MOVEMENT"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)
    assert entity.attrs["train_ident"] == "8195"
    assert entity.attrs["activity_type"] == "Avgang"
    assert entity.attrs["canceled"] is False


@pytest.mark.asyncio
async def test_situation_and_camera_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check: the classes this
    module maps to must actually exist as ClassDef leaves."""
    situations = _situation_response("DEV-VALIDATE", STOCKHOLM)["RESPONSE"]["RESULT"][0]["Situation"]
    cameras = _camera_response("CAM-VALIDATE", STOCKHOLM)["RESPONSE"]["RESULT"][0]["RoadConditionCamera"]
    for entity in _situations_to_entities(situations) + _cameras_to_entities(cameras):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_situations_is_idempotent_on_repeated_poll(db_session):
    entity_id = "TRAFIKVERKET-SITUATION-DEV-IDEMPOTENT"
    try:
        response = _situation_response("DEV-IDEMPOTENT", STOCKHOLM)
        for _ in range(2):
            client = _mock_client(response)
            try:
                counts = await poll_situations(db_session, client=client)
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


@pytest.mark.asyncio
async def test_poll_cameras_is_idempotent_on_repeated_poll(db_session):
    entity_id = "TRAFIKVERKET-CAMERA-CAM-IDEMPOTENT"
    try:
        response = _camera_response("CAM-IDEMPOTENT", STOCKHOLM)
        for _ in range(2):
            client = _mock_client(response)
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
