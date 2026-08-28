"""Entity name search: case-insensitive substring match on label/aliases."""
from neo4j import AsyncSession


async def search_entities_by_name(session: AsyncSession, q: str, limit: int = 20) -> list[dict]:
    result = await session.run(
        """
        MATCH (e:Entity)
        WHERE toLower(e.label) CONTAINS toLower($q)
           OR any(alias IN e.aliases WHERE toLower(alias) CONTAINS toLower($q))
        RETURN e
        ORDER BY e.label
        LIMIT $limit
        """,
        q=q,
        limit=limit,
    )
    return [dict(record["e"]) async for record in result]
