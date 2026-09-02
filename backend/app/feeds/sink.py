"""Direct MERGE-by-id writer for trusted, structured feed data.

Bypasses the LLM extraction/"proposed" hypothesis stage used by
app.api.ingest (see commit_batch): feed rows are already structured,
trusted observations from an authoritative source, not free text needing
extraction, so they write straight to committed graph state.
"""
import json

from neo4j import AsyncSession

from app.models.entity import EntityCreate


async def upsert_entities(session: AsyncSession, entities: list[EntityCreate]) -> int:
    """MERGE each entity by entity_id (same Cypher pattern as
    ingest.commit_batch), so repeated polls update the same node instead of
    duplicating it. Returns the number of entities written."""
    for entity in entities:
        await session.run(
            "MERGE (n:Entity {entity_id: $id}) SET n += $props",
            id=entity.entity_id,
            props={**entity.model_dump(mode="json"), "attrs": json.dumps(entity.attrs)},
        )
    return len(entities)
