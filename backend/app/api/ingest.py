import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.services.extraction_agent import extract_from_text

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    text: str


@router.post("")
async def ingest_text(body: IngestRequest):
    driver = get_driver()
    async with driver.session() as session:
        try:
            return await extract_from_text(session, body.text)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/{batch_id}/commit")
async def commit_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) RETURN b", id=batch_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="batch not found")
        batch = dict(record["b"])
        if batch["status"] != "proposed":
            raise HTTPException(status_code=409, detail=f"batch already {batch['status']}")

        entities = json.loads(batch["entities"])
        links = json.loads(batch["links"])

        for e in entities:
            entity = EntityCreate(**e)
            await session.run(
                "MERGE (n:Entity {entity_id: $id}) SET n += $props",
                id=entity.entity_id,
                props={**entity.model_dump(mode="json"), "attrs": json.dumps(entity.attrs)},
            )
        for l in links:
            link = LinkCreate(**l)
            await session.run(
                """
                MATCH (s:Entity {entity_id: $source_id}), (t:Entity {entity_id: $target_id})
                MERGE (s)-[r:LINK {link_id: $link_id}]->(t)
                SET r += $props
                """,
                source_id=link.source_entity,
                target_id=link.target_entity,
                link_id=link.link_id,
                props={**link.model_dump(mode="json"), "attrs": json.dumps(link.attrs)},
            )

        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.status = 'committed'", id=batch_id
        )
        return {"batch_id": batch_id, "status": "committed", "entities": len(entities), "links": len(links)}
