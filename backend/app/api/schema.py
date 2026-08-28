from fastapi import APIRouter, HTTPException

from app.db.neo4j_client import get_driver
from app.models.schema import ClassDef, ClassDefCreate, LinkDef, LinkDefCreate

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/classes", response_model=list[ClassDef])
async def list_classes():
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (c:ClassDef) RETURN c ORDER BY c.key")
        return [ClassDef(**dict(record["c"])) async for record in result]


@router.post("/classes", response_model=ClassDef, status_code=201)
async def create_class(class_def: ClassDefCreate):
    driver = get_driver()
    async with driver.session() as session:
        existing = await session.run("MATCH (c:ClassDef {key: $key}) RETURN c", key=class_def.key)
        if await existing.single() is not None:
            raise HTTPException(status_code=409, detail=f"class '{class_def.key}' already exists")

        if class_def.parent_key is not None:
            parent = await session.run(
                "MATCH (c:ClassDef {key: $key}) RETURN c", key=class_def.parent_key
            )
            if await parent.single() is None:
                raise HTTPException(
                    status_code=422, detail=f"parent_key '{class_def.parent_key}' does not exist"
                )
            level_row = await (
                await session.run(
                    "MATCH (c:ClassDef {key: $key}) RETURN c.level AS level", key=class_def.parent_key
                )
            ).single()
            level = level_row["level"] + 1
        else:
            level = 1

        result = await session.run(
            """
            CREATE (c:ClassDef {key: $key, parent_key: $parent_key, level: $level,
                                 label: $label, notes: $notes})
            WITH c
            OPTIONAL MATCH (p:ClassDef {key: $parent_key})
            FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
                MERGE (c)-[:SUBCLASS_OF]->(p))
            RETURN c
            """,
            key=class_def.key,
            parent_key=class_def.parent_key,
            level=level,
            label=class_def.label,
            notes=class_def.notes,
        )
        record = await result.single()
        return ClassDef(**dict(record["c"]))


@router.get("/links", response_model=list[LinkDef])
async def list_links():
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (l:LinkDef) RETURN l ORDER BY l.type")
        return [LinkDef(**dict(record["l"])) async for record in result]


@router.post("/links", response_model=LinkDef, status_code=201)
async def create_link_def(link_def: LinkDefCreate):
    driver = get_driver()
    async with driver.session() as session:
        existing = await session.run("MATCH (l:LinkDef {type: $type}) RETURN l", type=link_def.type)
        if await existing.single() is not None:
            raise HTTPException(status_code=409, detail=f"link type '{link_def.type}' already exists")

        result = await session.run(
            "CREATE (l:LinkDef $props) RETURN l", props=link_def.model_dump(mode="json")
        )
        record = await result.single()
        return LinkDef(**dict(record["l"]))
