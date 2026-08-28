import json

from fastapi import APIRouter, HTTPException, Query

from app.db.neo4j_client import get_driver
from app.models.link import Link, LinkCreate
from app.ontology.validate import ValidationError, validate_link

router = APIRouter(prefix="/links", tags=["links"])


def _to_props(data: dict) -> dict:
    return {**data, "attrs": json.dumps(data.get("attrs", {}))}


def _row_to_link(row: dict) -> Link:
    row = {**row, "attrs": json.loads(row.get("attrs") or "{}")}
    return Link(**row)


@router.post("", response_model=Link, status_code=201)
async def create_link(link: LinkCreate):
    driver = get_driver()
    async with driver.session() as session:
        source = await (
            await session.run(
                "MATCH (e:Entity {entity_id: $id}) RETURN e.entity_subclass AS c", id=link.source_entity
            )
        ).single()
        target = await (
            await session.run(
                "MATCH (e:Entity {entity_id: $id}) RETURN e.entity_subclass AS c", id=link.target_entity
            )
        ).single()
        if source is None:
            raise HTTPException(status_code=422, detail=f"source_entity '{link.source_entity}' not found")
        if target is None:
            raise HTTPException(status_code=422, detail=f"target_entity '{link.target_entity}' not found")

        try:
            await validate_link(session, link, source["c"], target["c"])
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        existing = await session.run("MATCH (l:Link {link_id: $id}) RETURN l", id=link.link_id)
        if await existing.single() is not None:
            raise HTTPException(status_code=409, detail=f"link_id '{link.link_id}' already exists")

        result = await session.run(
            """
            MATCH (s:Entity {entity_id: $source_id}), (t:Entity {entity_id: $target_id})
            CREATE (s)-[r:LINK $props]->(t)
            RETURN r
            """,
            source_id=link.source_entity,
            target_id=link.target_entity,
            props=_to_props(link.model_dump(mode="json")),
        )
        record = await result.single()
        return _row_to_link(dict(record["r"]))


@router.get("", response_model=list[Link])
async def list_links(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    link_type: str | None = Query(None),
):
    """Browse-all listing, stable order by link_id, with optional link_type
    filter and simple offset pagination."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH ()-[r:LINK]->()
            WHERE $link_type IS NULL OR r.link_type = $link_type
            RETURN r
            ORDER BY r.link_id
            SKIP $offset
            LIMIT $limit
            """,
            link_type=link_type,
            offset=offset,
            limit=limit,
        )
        return [_row_to_link(dict(record["r"])) async for record in result]


@router.get("/{link_id}", response_model=Link)
async def get_link(link_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH ()-[r:LINK {link_id: $id}]->() RETURN r", id=link_id)
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="link not found")
        return _row_to_link(dict(record["r"]))
