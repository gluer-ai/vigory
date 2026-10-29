import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.ontology.validate import ValidationError, validate_entity, validate_link
from app.services.duplicates import DuplicateDecisionError, apply_decisions, find_duplicates
from app.services.entity_resolution import find_synonym_match
from app.services.extraction_agent import (
    create_pending_batch,
    extract_from_text,
    process_text_batch,
    resolve_rejected,
)
from app.services.provenance import batch_source_id, ingest_source

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    text: str


class DuplicateDecision(BaseModel):
    entity_id: str
    action: str  # "keep_separate" | "remove"
    target_id: str | None = None  # remove: the entity this one duplicates


class ResolveDuplicatesRequest(BaseModel):
    decisions: list[DuplicateDecision]


async def _get_batch(session, batch_id: str) -> dict:
    result = await session.run(
        "MATCH (b:IngestBatch {batch_id: $id}) RETURN b", id=batch_id
    )
    record = await result.single()
    if record is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return dict(record["b"])


def _require_proposed(batch: dict) -> None:
    if batch["status"] != "proposed":
        raise HTTPException(status_code=409, detail=f"batch already {batch['status']}")


def _batch_response(batch: dict) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "stage": batch.get("stage"),
        "progress_current": batch.get("progress_current"),
        "progress_total": batch.get("progress_total"),
        "error_message": batch.get("error_message"),
        "source_text": batch.get("source_text", ""),
        "entities": json.loads(batch["entities"]),
        "links": json.loads(batch["links"]),
        "rejected_entities": json.loads(batch.get("rejected_entities") or "[]"),
        "rejected_links": json.loads(batch.get("rejected_links") or "[]"),
    }


def _decisions(batch: dict) -> dict:
    return json.loads(batch.get("duplicate_decisions") or "{}")


@router.post("")
async def ingest_text(body: IngestRequest):
    driver = get_driver()
    async with driver.session() as session:
        try:
            return await extract_from_text(session, body.text)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/async")
async def ingest_text_async(body: IngestRequest, background_tasks: BackgroundTasks):
    """Start extraction in the background and return the batch immediately with
    status='processing'. Poll GET /ingest/{batch_id} for stage/progress until it
    becomes 'proposed' (or 'error'). Needed for slow self-hosted models."""
    driver = get_driver()
    async with driver.session() as session:
        batch = await create_pending_batch(session, body.text)
    background_tasks.add_task(process_text_batch, batch["batch_id"])
    return batch


@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        return _batch_response(batch)


@router.get("/{batch_id}/entities/suggest")
async def suggest_entity(
    batch_id: str,
    label: str = Query(...),
    entity_class: str = Query(...),
    aliases: str = Query(""),
):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        batch_entities = json.loads(batch["entities"])
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
        try:
            match = await find_synonym_match(
                session, label, entity_class, alias_list, batch_entities
            )
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if match is None:
            return {"match": None, "reason": None}
        return {
            "match": {
                "entity_id": match["entity_id"],
                "label": match["label"],
                "aliases": match.get("aliases", []),
                "entity_class": match.get("entity_class", entity_class),
                "entity_subclass": match.get("entity_subclass", ""),
            },
            "reason": match.get("reason", ""),
        }


@router.post("/{batch_id}/entities/classify")
async def classify_entities(batch_id: str):
    """Place rejected entities/links: map to existing ontology terms or let the
    agent extend the ontology (new terms are validated and tagged
    origin=extraction). Used by the review panel's bulk-create buttons."""
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        try:
            with ingest_source(batch_source_id(batch)):
                return await resolve_rejected(
                    session,
                    json.loads(batch["entities"]),
                    json.loads(batch.get("rejected_entities") or "[]"),
                    json.loads(batch.get("rejected_links") or "[]"),
                )
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.get("/{batch_id}/duplicates")
async def list_duplicates(batch_id: str):
    """Proposed entities that look like duplicates of something already in the
    graph or earlier in this batch and still need a human decision."""
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        duplicates = await find_duplicates(session, json.loads(batch["entities"]), _decisions(batch))
        return {"duplicates": duplicates}


@router.post("/{batch_id}/duplicates/resolve")
async def resolve_duplicates(batch_id: str, body: ResolveDuplicatesRequest):
    """Apply the reviewer's decisions: keep_separate (it is a different thing)
    or remove (same thing: drop the proposed copy, re-point its links at the
    entity it duplicates). Returns the updated batch."""
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)
        entities = json.loads(batch["entities"])

        wanted = {d.entity_id for d in body.decisions}
        ids = {e["entity_id"] for e in entities}
        targets = {d.target_id for d in body.decisions if d.target_id} - ids
        probe = sorted(wanted | targets)
        found = await session.run(
            "MATCH (e:Entity) WHERE e.entity_id IN $ids RETURN e.entity_id AS id", ids=probe
        )
        in_graph = {r["id"] async for r in found}

        def unused_id(base: str, taken: set[str]) -> str:
            n = 2
            while f"{base}-{n}" in taken:
                n += 1
            return f"{base}-{n}"

        try:
            new_entities, new_links, decisions = apply_decisions(
                entities,
                json.loads(batch["links"]),
                _decisions(batch),
                [d.model_dump() for d in body.decisions],
                known_target_ids=in_graph,
                unused_id=unused_id,
            )
        except DuplicateDecisionError as e:
            raise HTTPException(status_code=422, detail=str(e))

        await session.run(
            """
            MATCH (b:IngestBatch {batch_id: $id})
            SET b.entities = $entities, b.links = $links, b.duplicate_decisions = $decisions
            """,
            id=batch_id,
            entities=json.dumps(new_entities),
            links=json.dumps(new_links),
            decisions=json.dumps(decisions),
        )
        batch.update(
            entities=json.dumps(new_entities),
            links=json.dumps(new_links),
            duplicate_decisions=json.dumps(decisions),
        )
        return _batch_response(batch)


@router.post("/{batch_id}/entities")
async def add_batch_entity(batch_id: str, entity: EntityCreate):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        batch_entities = json.loads(batch["entities"])
        if any(e["entity_id"] == entity.entity_id for e in batch_entities):
            raise HTTPException(
                status_code=409,
                detail=f"entity_id '{entity.entity_id}' already used in this batch",
            )
        existing = await session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity.entity_id
        )
        if await existing.single() is not None:
            raise HTTPException(
                status_code=409, detail=f"entity_id '{entity.entity_id}' already exists"
            )

        try:
            await validate_entity(session, entity)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        batch_entities.append(entity.model_dump(mode="json"))
        new_entities_json = json.dumps(batch_entities)
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.entities = $entities",
            id=batch_id,
            entities=new_entities_json,
        )
        batch["entities"] = new_entities_json
        return _batch_response(batch)


@router.post("/{batch_id}/links")
async def add_batch_link(batch_id: str, link: LinkCreate):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        batch_links = json.loads(batch["links"])
        if any(l["link_id"] == link.link_id for l in batch_links):
            raise HTTPException(
                status_code=409, detail=f"link_id '{link.link_id}' already used in this batch"
            )
        existing_link = await session.run(
            "MATCH ()-[r:LINK {link_id: $id}]->() RETURN r LIMIT 1", id=link.link_id
        )
        if await existing_link.single() is not None:
            raise HTTPException(
                status_code=409, detail=f"link_id '{link.link_id}' already exists"
            )

        batch_entities = json.loads(batch["entities"])
        class_by_id = {e["entity_id"]: e["entity_class"] for e in batch_entities}
        for endpoint_id in (link.source_entity, link.target_entity):
            if endpoint_id in class_by_id:
                continue
            result = await session.run(
                "MATCH (e:Entity {entity_id: $id}) RETURN e", id=endpoint_id
            )
            record = await result.single()
            if record is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"link references entity '{endpoint_id}', which is neither in this "
                        "batch nor an existing committed entity"
                    ),
                )
            class_by_id[endpoint_id] = dict(record["e"])["entity_class"]

        try:
            await validate_link(
                session, link, class_by_id[link.source_entity], class_by_id[link.target_entity]
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        batch_links.append(link.model_dump(mode="json"))
        new_links_json = json.dumps(batch_links)
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.links = $links",
            id=batch_id,
            links=new_links_json,
        )
        batch["links"] = new_links_json
        return _batch_response(batch)


@router.post("/{batch_id}/commit")
async def commit_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        entities = json.loads(batch["entities"])
        links = json.loads(batch["links"])

        # Committing an unresolved duplicate would create a second copy - or,
        # when the entity_id already exists, silently overwrite that entity.
        unresolved = await find_duplicates(session, entities, _decisions(batch))
        if unresolved:
            names = ", ".join(d["label"] or d["entity_id"] for d in unresolved[:3])
            more = f" and {len(unresolved) - 3} more" if len(unresolved) > 3 else ""
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{len(unresolved)} possible duplicate(s) need a decision before commit: "
                    f"{names}{more}"
                ),
            )

        # Stamp what THIS import created so it can be removed with its source.
        # ON CREATE only: an entity that already existed keeps its own source.
        import_id = batch_source_id(batch)

        for e in entities:
            entity = EntityCreate(**e)
            await session.run(
                "MERGE (n:Entity {entity_id: $id}) ON CREATE SET n.source_id = $import_id SET n += $props",
                id=entity.entity_id,
                import_id=import_id,
                props={**entity.model_dump(mode="json"), "attrs": json.dumps(entity.attrs)},
            )
        for l in links:
            link = LinkCreate(**l)
            await session.run(
                """
                MATCH (s:Entity {entity_id: $source_id}), (t:Entity {entity_id: $target_id})
                MERGE (s)-[r:LINK {link_id: $link_id}]->(t)
                ON CREATE SET r.source_id = $import_id
                SET r += $props
                """,
                source_id=link.source_entity,
                target_id=link.target_entity,
                import_id=import_id,
                link_id=link.link_id,
                props={**link.model_dump(mode="json"), "attrs": json.dumps(link.attrs)},
            )

        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.status = 'committed'", id=batch_id
        )
        if batch.get("document_id"):
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = 'committed'",
                id=batch["document_id"],
            )
        return {"batch_id": batch_id, "status": "committed", "entities": len(entities), "links": len(links)}
