from fastapi import APIRouter, HTTPException

from app.db.neo4j_client import get_driver
from app.models.schema import (
    ClassDef,
    ClassDefCreate,
    ClassMerge,
    ClassMove,
    ClassUpdate,
    LinkDef,
    LinkDefCreate,
)

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


@router.get("/classes/counts")
async def class_entity_counts() -> dict[str, int]:
    """Entities per exact entity_subclass key (the UI rolls these up the tree)."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (e:Entity) RETURN e.entity_subclass AS key, count(e) AS n"
        )
        return {record["key"]: record["n"] async for record in result if record["key"]}


async def _require_class(session, key: str) -> dict:
    result = await session.run("MATCH (c:ClassDef {key: $key}) RETURN c", key=key)
    record = await result.single()
    if record is None:
        raise HTTPException(status_code=404, detail=f"class '{key}' not found")
    return dict(record["c"])


async def _usage(session, key: str) -> tuple[int, int]:
    """(child classes, entities) that depend on this class."""
    result = await session.run(
        """
        OPTIONAL MATCH (ch:ClassDef {parent_key: $key})
        WITH count(ch) AS children
        OPTIONAL MATCH (e:Entity {entity_subclass: $key})
        RETURN children, count(e) AS entities
        """,
        key=key,
    )
    record = await result.single()
    return record["children"], record["entities"]


@router.patch("/classes/{key}", response_model=ClassDef)
async def update_class(key: str, update: ClassUpdate):
    """Edit label/notes only. The key encodes the path and entities reference
    it, so re-parenting lives in the dedicated /move endpoint."""
    changes = update.model_dump(exclude_unset=True)
    if "label" in changes and changes["label"] is None:
        raise HTTPException(status_code=422, detail="label cannot be null")
    driver = get_driver()
    async with driver.session() as session:
        await _require_class(session, key)
        if not changes:
            return ClassDef(**await _require_class(session, key))
        result = await session.run(
            "MATCH (c:ClassDef {key: $key}) SET c += $changes RETURN c", key=key, changes=changes
        )
        record = await result.single()
        return ClassDef(**dict(record["c"]))


@router.delete("/classes/{key}", status_code=204)
async def delete_class(key: str):
    """Delete a class nothing depends on: not a root, no child classes, no entities."""
    driver = get_driver()
    async with driver.session() as session:
        cls = await _require_class(session, key)
        if cls.get("parent_key") is None:
            raise HTTPException(status_code=409, detail="root classes cannot be deleted")
        children, entities = await _usage(session, key)
        if children or entities:
            raise HTTPException(
                status_code=409,
                detail=f"class '{key}' is in use by {children} child class(es) and "
                f"{entities} entities; merge or remove them first",
            )
        await session.run("MATCH (c:ClassDef {key: $key}) DETACH DELETE c", key=key)


@router.post("/classes/{key}/merge")
async def merge_class(key: str, body: ClassMerge) -> dict[str, int | str]:
    """Fold a duplicate class into another: its entities move to the target and
    the duplicate is deleted, in one atomic statement."""
    if body.into == key:
        raise HTTPException(status_code=422, detail="cannot merge a class into itself")
    driver = get_driver()
    async with driver.session() as session:
        source = await _require_class(session, key)
        await _require_class(session, body.into)
        if source.get("parent_key") is None:
            raise HTTPException(status_code=409, detail="root classes cannot be merged")
        if body.into.startswith(key + "."):
            raise HTTPException(status_code=422, detail="cannot merge a class into its own descendant")
        children, _ = await _usage(session, key)
        if children:
            raise HTTPException(
                status_code=409,
                detail=f"class '{key}' has {children} child class(es); merge or delete them first",
            )
        result = await session.run(
            """
            MATCH (c:ClassDef {key: $key}), (t:ClassDef {key: $into})
            OPTIONAL MATCH (e:Entity {entity_subclass: $key})
            WITH c, t, collect(e) AS moved
            FOREACH (e IN moved | SET e.entity_subclass = t.key, e.entity_class = $root)
            DETACH DELETE c
            RETURN size(moved) AS moved
            """,
            key=key,
            into=body.into,
            root=body.into.split(".")[0],
        )
        record = await result.single()
        return {"merged": key, "into": body.into, "entities_moved": record["moved"]}


@router.post("/classes/{key}/move", response_model=ClassDef)
async def move_class(key: str, body: ClassMove):
    """Re-parent a class (and its whole subtree). Keys are paths that entities
    reference, so every class below `key` gets a new key and its entities are
    re-pointed - all in one statement, so a failure changes nothing."""
    driver = get_driver()
    async with driver.session() as session:
        source = await _require_class(session, key)
        await _require_class(session, body.parent_key)
        if source.get("parent_key") is None:
            raise HTTPException(status_code=409, detail="root classes cannot be moved")
        if body.parent_key == key or body.parent_key.startswith(key + "."):
            raise HTTPException(status_code=422, detail="cannot move a class into itself or its own descendant")
        if body.parent_key == source.get("parent_key"):
            raise HTTPException(status_code=422, detail="class is already under that parent")
        new_key = f"{body.parent_key}.{key.rsplit('.', 1)[-1]}"
        existing = await session.run("MATCH (c:ClassDef {key: $key}) RETURN c", key=new_key)
        if await existing.single() is not None:
            raise HTTPException(status_code=409, detail=f"class '{new_key}' already exists")
        result = await session.run(
            """
            MATCH (c:ClassDef) WHERE c.key = $key OR c.key STARTS WITH $prefix
            OPTIONAL MATCH (e:Entity) WHERE e.entity_subclass = c.key
            WITH c, collect(e) AS ents, c.key AS old, c.parent_key AS old_parent
            WITH c, ents, old,
                 $new_key + substring(old, size($key)) AS nk,
                 CASE WHEN old = $key THEN $new_parent
                      ELSE $new_key + substring(old_parent, size($key)) END AS np
            SET c.key = nk, c.parent_key = np, c.level = size(split(nk, '.'))
            FOREACH (e IN ents | SET e.entity_subclass = nk, e.entity_class = $root)
            WITH c, old WHERE old = $key
            OPTIONAL MATCH (c)-[r:SUBCLASS_OF]->(:ClassDef)
            DELETE r
            WITH DISTINCT c
            MATCH (p:ClassDef {key: $new_parent})
            MERGE (c)-[:SUBCLASS_OF]->(p)
            RETURN c
            """,
            key=key,
            prefix=key + ".",
            new_key=new_key,
            new_parent=body.parent_key,
            root=body.parent_key.split(".")[0],
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
