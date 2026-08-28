"""Integration test for the deterministic scoping traversal, against a small
seeded fixture graph. Requires Neo4j to be running (see docker-compose.yml).
"""
import json
import uuid

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.services.scoping import scope_subgraph


@pytest_asyncio.fixture
async def seeded_session():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    suffix = uuid.uuid4().hex[:8]
    ids = {name: f"TEST-{suffix}-{name}" for name in ["A", "B", "C", "D"]}

    async with driver.session() as session:
        for key, entity_id in ids.items():
            await session.run(
                """
                CREATE (:Entity {entity_id: $id, entity_class: 'PERSON',
                                  entity_subclass: 'PERSON.CIVILIAN', label: $id,
                                  status: 'active', confidence: 'B2', source_ref: 'TEST',
                                  aliases: [], attrs: $attrs})
                """,
                id=entity_id,
                attrs=json.dumps({}),
            )
        # A - B - C (2 hops from A), D is disconnected
        await session.run(
            """
            MATCH (a:Entity {entity_id: $a}), (b:Entity {entity_id: $b})
            CREATE (a)-[:LINK {link_id: $lid1, link_type: 'associated_with',
                                direction: 'symmetric', assertion_status: 'reported',
                                confidence: 'B2', source_ref: 'TEST', attrs: $attrs}]->(b)
            """,
            a=ids["A"],
            b=ids["B"],
            lid1=f"TEST-{suffix}-L1",
            attrs=json.dumps({}),
        )
        await session.run(
            """
            MATCH (b:Entity {entity_id: $b}), (c:Entity {entity_id: $c})
            CREATE (b)-[:LINK {link_id: $lid2, link_type: 'associated_with',
                                direction: 'symmetric', assertion_status: 'reported',
                                confidence: 'B2', source_ref: 'TEST', attrs: $attrs}]->(c)
            """,
            b=ids["B"],
            c=ids["C"],
            lid2=f"TEST-{suffix}-L2",
            attrs=json.dumps({}),
        )

        yield session, ids

        for entity_id in ids.values():
            await session.run(
                "MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id
            )
    await driver.close()


@pytest.mark.asyncio
async def test_scope_one_hop_excludes_two_hop_entity(seeded_session):
    session, ids = seeded_session
    result = await scope_subgraph(session, ids["A"], hops=1)
    node_ids = {n["entity_id"] for n in result["nodes"]}
    assert node_ids == {ids["A"], ids["B"]}


@pytest.mark.asyncio
async def test_scope_two_hops_includes_transitive_entity(seeded_session):
    session, ids = seeded_session
    result = await scope_subgraph(session, ids["A"], hops=2)
    node_ids = {n["entity_id"] for n in result["nodes"]}
    assert node_ids == {ids["A"], ids["B"], ids["C"]}
    assert ids["D"] not in node_ids


@pytest.mark.asyncio
async def test_scope_excludes_disconnected_entity(seeded_session):
    session, ids = seeded_session
    result = await scope_subgraph(session, ids["A"], hops=5)
    node_ids = {n["entity_id"] for n in result["nodes"]}
    assert ids["D"] not in node_ids
