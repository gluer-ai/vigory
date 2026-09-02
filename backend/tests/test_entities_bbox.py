"""Integration test for GET /entities?bbox=..., against a small seeded
fixture graph. Requires Neo4j to be running (see docker-compose.yml).
"""
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.main import create_app

# Deliberately far from Sweden: every live VEHICLE-class feed (aircraft,
# vessels) is bbox-scoped to Sweden (see app/feeds/bbox.py SWEDEN_BBOX), so
# a fixture placed there gets buried under real polled traffic and its
# entity_id can be paginated out. Montevideo is used purely as "nowhere
# near Sweden", not because it's meaningful.
TEST_BBOX = (-40.0, -30.0, -60.0, -50.0)  # lat_min, lat_max, lon_min, lon_max
MONTEVIDEO = (-34.9, -56.2)  # inside TEST_BBOX
BERLIN = (52.5, 13.4)  # outside it (and outside Sweden too)


@pytest_asyncio.fixture
async def seeded_entities():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    suffix = uuid.uuid4().hex[:8]
    in_bbox_id = f"TEST-{suffix}-stockholm"
    out_of_bbox_id = f"TEST-{suffix}-berlin"
    no_coords_id = f"TEST-{suffix}-nocoords"

    async with driver.session() as session:
        for entity_id, (lat, lon) in [(in_bbox_id, MONTEVIDEO), (out_of_bbox_id, BERLIN)]:
            await session.run(
                """
                CREATE (:Entity {entity_id: $id, entity_class: 'VEHICLE',
                                  entity_subclass: 'VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT',
                                  label: $id, status: 'active', confidence: 'B2',
                                  source_ref: 'TEST', aliases: [], attrs: $attrs})
                """,
                id=entity_id,
                attrs=json.dumps({"lat": lat, "lon": lon}),
            )
        await session.run(
            """
            CREATE (:Entity {entity_id: $id, entity_class: 'VEHICLE',
                              entity_subclass: 'VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT',
                              label: $id, status: 'active', confidence: 'B2',
                              source_ref: 'TEST', aliases: [], attrs: $attrs})
            """,
            id=no_coords_id,
            attrs=json.dumps({}),
        )

        yield suffix, in_bbox_id, out_of_bbox_id, no_coords_id

        for entity_id in (in_bbox_id, out_of_bbox_id, no_coords_id):
            await session.run("MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id)
    await driver.close()


@pytest.mark.asyncio
async def test_list_entities_bbox_filters_by_lat_lon(seeded_entities):
    _, in_bbox_id, out_of_bbox_id, no_coords_id = seeded_entities
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/entities", params={"limit": 200, "bbox": ",".join(str(v) for v in TEST_BBOX), "entity_class": "VEHICLE"}
        )
    assert resp.status_code == 200
    ids = {row["entity_id"] for row in resp.json()}
    assert in_bbox_id in ids
    assert out_of_bbox_id not in ids
    assert no_coords_id not in ids


@pytest.mark.asyncio
async def test_list_entities_rejects_malformed_bbox():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/entities", params={"bbox": "not-a-bbox"})
    assert resp.status_code == 422
