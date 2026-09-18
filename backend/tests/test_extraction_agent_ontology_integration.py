"""Integration test for _fetch_ontology_vocab's leaf-only entity_subclass
filtering, against the real seeded ontology graph. Requires Neo4j running
with the ontology imported (see docker-compose.yml + ontology/import_ontology.py).
"""
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.services.extraction_agent import _fetch_ontology_vocab


@pytest_asyncio.fixture
async def real_session():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
    await driver.close()


@pytest.mark.asyncio
async def test_fetch_ontology_vocab_only_includes_leaf_entity_subclasses(real_session):
    class_keys, _, _ = await _fetch_ontology_vocab(real_session)

    # PERSON.MILITARY_PERSONNEL has children (COMMISSIONED_OFFICER etc.) in
    # the seeded ontology, so it — and the PERSON root above it — should be
    # excluded from the prompt's vocabulary: only the most-specific leaf
    # descendants are offered, cutting prompt size and ambiguity.
    assert "PERSON.MILITARY_PERSONNEL" not in class_keys
    assert "PERSON" not in class_keys

    # The leaves themselves must still be present and pickable.
    assert "PERSON.MILITARY_PERSONNEL.COMMISSIONED_OFFICER" in class_keys
    assert "ORGANIZATION.COMMERCIAL_ENTITY.FINANCIAL_INSTITUTION" in class_keys
