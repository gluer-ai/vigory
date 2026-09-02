"""Tests for the aisstream.io vessel feed: entity mapping against the live
ontology and MERGE idempotency. The WebSocket is faked via an injectable
ws_connect factory yielding a fixed, finite sequence of PositionReport
messages — no real network, no timeout dependency. Requires Neo4j running.
"""
import json

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.aisstream import _message_to_entity, poll_vessels
from app.ontology.validate import validate_entity

POSITION_REPORT = {
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 265547250, "ShipName": "TESTSHIP", "Latitude": 59.33, "Longitude": 18.06},
    "Message": {"PositionReport": {"Sog": 12.4, "Cog": 86.7, "NavigationalStatus": 0}},
}


class _FakeWebSocket:
    """Fakes the subset of a websockets connection used by aisstream.py:
    send(), and async-iteration over a fixed, finite message sequence."""

    def __init__(self, messages: list[dict]):
        self._messages = messages
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for m in self._messages:
            yield json.dumps(m)


class _FakeConnect:
    """Injectable stand-in for websockets.connect: an async context manager
    factory that returns a fixed sequence of messages regardless of URL."""

    def __init__(self, messages: list[dict]):
        self._messages = messages

    def __call__(self, url: str):
        return self

    async def __aenter__(self):
        return _FakeWebSocket(self._messages)

    async def __aexit__(self, *exc):
        return False


@pytest_asyncio.fixture
async def db_session():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
    await driver.close()


def test_message_to_entity_maps_position_report():
    entity = _message_to_entity(POSITION_REPORT)
    assert entity.entity_id == "AISSTREAM-265547250"
    assert entity.entity_class == "VEHICLE"
    assert entity.entity_subclass == "VEHICLE.SEA_VEHICLE.MERCHANT_VESSEL"
    assert entity.label == "TESTSHIP"
    assert entity.attrs["lat"] == pytest.approx(59.33)
    assert entity.attrs["lon"] == pytest.approx(18.06)
    assert entity.attrs["sog"] == pytest.approx(12.4)
    assert entity.attrs["mmsi"] == 265547250


def test_message_to_entity_ignores_non_position_report():
    assert _message_to_entity({"MessageType": "SubscriptionConfirmation"}) is None


@pytest.mark.asyncio
async def test_vessel_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check: VEHICLE.SEA_VEHICLE.MERCHANT_VESSEL
    must actually exist as a ClassDef key."""
    await validate_entity(db_session, _message_to_entity(POSITION_REPORT))  # no raise


@pytest.mark.asyncio
async def test_poll_vessels_is_idempotent_on_repeated_poll(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "aisstream_api_key", "test-key")
    entity_id = "AISSTREAM-265547250"
    try:
        for _ in range(2):
            counts = await poll_vessels(db_session, ws_connect=_FakeConnect([POSITION_REPORT]))
            assert counts == {"fetched": 1, "written": 1}

        result = await db_session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN count(e) AS c", id=entity_id
        )
        record = await result.single()
        assert record["c"] == 1
    finally:
        await db_session.run("MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id)
