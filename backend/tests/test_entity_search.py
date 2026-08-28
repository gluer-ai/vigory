"""Integration test for entity name search, against a small seeded fixture
graph. Requires Neo4j to be running (see docker-compose.yml).
"""
import json
import uuid

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.services.search import search_entities_by_name


@pytest_asyncio.fixture
async def seeded_entities():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    suffix = uuid.uuid4().hex[:8]
    petrov_id = f"TEST-{suffix}-petrov"
    other_id = f"TEST-{suffix}-other"

    async with driver.session() as session:
        await session.run(
            """
            CREATE (:Entity {entity_id: $id, entity_class: 'PERSON',
                              entity_subclass: 'PERSON.CIVILIAN', label: $label,
                              status: 'active', confidence: 'B2', source_ref: 'TEST',
                              aliases: [], attrs: $attrs})
            """,
            id=petrov_id,
            label=f"Ivan Petrov {suffix}",
            attrs=json.dumps({}),
        )
        await session.run(
            """
            CREATE (:Entity {entity_id: $id, entity_class: 'ORGANIZATION',
                              entity_subclass: 'ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION',
                              label: $label, status: 'active', confidence: 'B2',
                              source_ref: 'TEST', aliases: [], attrs: $attrs})
            """,
            id=other_id,
            label=f"Unrelated Battalion {suffix}",
            attrs=json.dumps({}),
        )

        yield session, suffix, petrov_id, other_id

        for entity_id in (petrov_id, other_id):
            await session.run(
                "MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id
            )
    await driver.close()


@pytest.mark.asyncio
async def test_search_matches_by_name(seeded_entities):
    session, suffix, petrov_id, other_id = seeded_entities
    results = await search_entities_by_name(session, f"Ivan Petrov {suffix}")
    ids = {r["entity_id"] for r in results}
    assert petrov_id in ids
    assert other_id not in ids


@pytest.mark.asyncio
async def test_search_is_case_insensitive(seeded_entities):
    session, suffix, petrov_id, _ = seeded_entities
    results = await search_entities_by_name(session, f"IVAN petrov {suffix}".upper())
    ids = {r["entity_id"] for r in results}
    assert petrov_id in ids


@pytest.mark.asyncio
async def test_search_no_match_returns_empty_list(seeded_entities):
    session, _, _, _ = seeded_entities
    results = await search_entities_by_name(session, "no-entity-matches-this-string-xyz-000")
    assert results == []
