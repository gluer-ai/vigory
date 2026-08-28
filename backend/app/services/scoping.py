"""Deterministic N-hop scenario scoping: the source of truth for "what's in
scope" around a trigger entity. No LLM involved — pure Cypher traversal.
"""
from neo4j import AsyncSession


async def scope_subgraph(
    session: AsyncSession,
    trigger_entity_id: str,
    hops: int = 2,
    link_types: list[str] | None = None,
) -> dict:
    hops = max(1, min(hops, 5))  # simplification: hard cap avoids unbounded traversal cost
    type_filter = ""
    params: dict = {"id": trigger_entity_id}
    if link_types:
        type_filter = "AND ALL(r IN relationships(path) WHERE r.link_type IN $link_types)"
        params["link_types"] = link_types

    query = f"""
        MATCH (trigger:Entity {{entity_id: $id}})
        OPTIONAL MATCH path = (trigger)-[:LINK*1..{hops}]-(other:Entity)
        WHERE other IS NULL OR (true {type_filter})
        WITH trigger,
             collect(DISTINCT other) AS others,
             collect(DISTINCT path) AS paths
        RETURN trigger, others, paths
    """
    result = await session.run(query, **params)
    record = await result.single()
    if record is None or record["trigger"] is None:
        return {"nodes": [], "edges": []}

    nodes_by_id: dict[str, dict] = {}
    trigger_node = dict(record["trigger"])
    nodes_by_id[trigger_node["entity_id"]] = trigger_node
    for node in record["others"]:
        if node is not None:
            d = dict(node)
            nodes_by_id[d["entity_id"]] = d

    edges_by_id: dict[str, dict] = {}
    for path in record["paths"]:
        if path is None:
            continue
        for rel in path.relationships:
            d = dict(rel)
            edges_by_id[d["link_id"]] = d

    return {"nodes": list(nodes_by_id.values()), "edges": list(edges_by_id.values())}
