import json

from fastapi import APIRouter, HTTPException, Query

from app.db.neo4j_client import get_driver
from app.feeds.bbox import in_bbox
from app.models.entity import Entity, EntityCreate, EntityUpdate
from app.ontology.validate import ValidationError, validate_entity
from app.services.search import search_entities_by_name

router = APIRouter(prefix="/entities", tags=["entities"])

# Cap on rows scanned in Python when a bbox filter is applied (see
# list_entities). Keeps a single request bounded regardless of graph size.
_BBOX_SCAN_LIMIT = 5000


def _to_props(data: dict) -> dict:
    """Neo4j node properties can't hold nested maps: JSON-encode attrs."""
    return {**data, "attrs": json.dumps(data.get("attrs", {}))}


def _row_to_entity(row: dict) -> Entity:
    row = {**row, "attrs": json.loads(row.get("attrs") or "{}")}
    return Entity(**row)


@router.post("", response_model=Entity, status_code=201)
async def create_entity(entity: EntityCreate):
    driver = get_driver()
    async with driver.session() as session:
        try:
            await validate_entity(session, entity)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        existing = await session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity.entity_id
        )
        if await existing.single() is not None:
            raise HTTPException(status_code=409, detail=f"entity_id '{entity.entity_id}' already exists")

        result = await session.run(
            """
            CREATE (e:Entity $props)
            RETURN e
            """,
            props=_to_props(entity.model_dump(mode="json")),
        )
        record = await result.single()
        return _row_to_entity(dict(record["e"]))


@router.get("", response_model=list[Entity])
async def list_entities(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    entity_class: str | None = Query(None),
    bbox: str | None = Query(
        None, description="latmin,latmax,lonmin,lonmax — keeps entities whose attrs.lat/attrs.lon fall inside"
    ),
):
    """Browse-all listing, stable order by entity_id, with optional
    root-class filter, bbox filter, and simple offset pagination."""
    parsed_bbox = _parse_bbox(bbox)
    driver = get_driver()
    async with driver.session() as session:
        if parsed_bbox is None:
            result = await session.run(
                """
                MATCH (e:Entity)
                WHERE $entity_class IS NULL OR e.entity_class = $entity_class
                RETURN e
                ORDER BY e.entity_id
                SKIP $offset
                LIMIT $limit
                """,
                entity_class=entity_class,
                offset=offset,
                limit=limit,
            )
            return [_row_to_entity(dict(record["e"])) async for record in result]

        # simplification: lat/lon live inside the JSON-encoded attrs blob, not
        # as indexed top-level properties, so Cypher can't filter on them
        # directly. Scan up to _BBOX_SCAN_LIMIT candidates (already narrowed
        # by entity_class) and filter/paginate in Python. Upgrade path: store
        # lat/lon as top-level float properties on Entity if this needs to
        # scale past a few thousand geo-tagged entities.
        result = await session.run(
            """
            MATCH (e:Entity)
            WHERE $entity_class IS NULL OR e.entity_class = $entity_class
            RETURN e
            ORDER BY e.entity_id
            LIMIT $scan_limit
            """,
            entity_class=entity_class,
            scan_limit=_BBOX_SCAN_LIMIT,
        )
        entities = [_row_to_entity(dict(record["e"])) async for record in result]
        matches = [
            e
            for e in entities
            if isinstance(e.attrs.get("lat"), (int, float))
            and isinstance(e.attrs.get("lon"), (int, float))
            and in_bbox(e.attrs["lat"], e.attrs["lon"], parsed_bbox)
        ]
        return matches[offset : offset + limit]


def _parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=422, detail="bbox must be 'latmin,latmax,lonmin,lonmax'")
    try:
        lat_min, lat_max, lon_min, lon_max = (float(p) for p in parts)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox must be 'latmin,latmax,lonmin,lonmax'")
    return (lat_min, lat_max, lon_min, lon_max)


@router.get("/search", response_model=list[Entity])
async def search_entities(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    """Case-insensitive substring match on label or aliases — the name-search
    counterpart to looking an entity up by its exact entity_id."""
    driver = get_driver()
    async with driver.session() as session:
        rows = await search_entities_by_name(session, q, limit)
        return [_row_to_entity(row) for row in rows]


@router.get("/{entity_id}", response_model=Entity)
async def get_entity(entity_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity_id)
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="entity not found")
        return _row_to_entity(dict(record["e"]))


@router.patch("/{entity_id}", response_model=Entity)
async def update_entity(entity_id: str, patch: EntityUpdate):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity_id)
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="entity not found")

        updates = patch.model_dump(mode="json", exclude_unset=True)
        if updates:
            current = _row_to_entity(dict(record["e"])).model_dump(mode="json")
            candidate = Entity(**{**current, **updates})
            try:
                await validate_entity(session, candidate)
            except ValidationError as e:
                raise HTTPException(status_code=422, detail=str(e))

            result = await session.run(
                "MATCH (e:Entity {entity_id: $id}) SET e += $updates RETURN e",
                id=entity_id,
                updates=_to_props(updates) if "attrs" in updates else updates,
            )
            record = await result.single()
        return _row_to_entity(dict(record["e"]))
