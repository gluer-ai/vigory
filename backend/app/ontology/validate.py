"""Validation against the Neo4j-resident ontology (ClassDef/LinkDef/VocabValue).

Nothing here is hardcoded to the taxonomy's specific classes/links: every rule
reads the current graph state, so extending the ontology at runtime (via
POST /schema/classes) immediately changes what validates.
"""
from neo4j import AsyncSession

from app.models.entity import EntityBase
from app.models.link import LinkBase


class ValidationError(Exception):
    pass


async def _class_exists(session: AsyncSession, key: str) -> bool:
    result = await session.run("MATCH (c:ClassDef {key: $key}) RETURN c LIMIT 1", key=key)
    return await result.single() is not None


async def _class_root(session: AsyncSession, key: str) -> str | None:
    """Root class label for a full taxonomy key, e.g. PERSON.MILITARY -> PERSON."""
    result = await session.run(
        """
        MATCH (c:ClassDef {key: $key})
        OPTIONAL MATCH path = (c)-[:SUBCLASS_OF*0..]->(root:ClassDef)
        WHERE NOT (root)-[:SUBCLASS_OF]->()
        RETURN root.label AS root_label
        """,
        key=key,
    )
    record = await result.single()
    return record["root_label"] if record else None


async def validate_entity(session: AsyncSession, entity: EntityBase) -> None:
    if not await _class_exists(session, entity.entity_subclass):
        raise ValidationError(
            f"entity_subclass '{entity.entity_subclass}' is not a known ClassDef key"
        )

    vocab = await session.run(
        "MATCH (v:VocabValue {list_name: 'entity_status', value: $status}) RETURN v LIMIT 1",
        status=entity.status,
    )
    if await vocab.single() is None:
        raise ValidationError(f"status '{entity.status}' is not in the entity_status vocabulary")


def _split_class_list(value: str) -> list[str]:
    """Domain/range columns hold either 'Any' or a '; '-separated list of
    root class labels (e.g. 'Person; Organization') — never a single class
    name to compare verbatim."""
    return [v.strip().lower() for v in value.split(";")]


async def validate_link(
    session: AsyncSession, link: LinkBase, source_class: str, target_class: str
) -> None:
    result = await session.run(
        "MATCH (l:LinkDef {type: $type}) RETURN l LIMIT 1", type=link.link_type
    )
    linkdef = await result.single()
    if linkdef is None:
        raise ValidationError(f"link_type '{link.link_type}' is not a known LinkDef")

    domain = linkdef["l"]["domain"]
    range_ = linkdef["l"]["range"]

    if domain != "Any":
        source_root = await _class_root(session, source_class)
        if source_root is None or source_root.lower() not in _split_class_list(domain):
            raise ValidationError(
                f"link '{link.link_type}' requires domain '{domain}', "
                f"but source entity is class '{source_root}'"
            )

    if range_ != "Any":
        target_root = await _class_root(session, target_class)
        if target_root is None or target_root.lower() not in _split_class_list(range_):
            raise ValidationError(
                f"link '{link.link_type}' requires range '{range_}', "
                f"but target entity is class '{target_root}'"
            )
