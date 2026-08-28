"""Scenario ranking/explain agent: layered on top of the deterministic scope
(services/scoping.py). It can only annotate or drop candidates the graph
already returned — never add new nodes/edges, so it can't hallucinate scope.
"""
from app.llm.client import complete_json

SYSTEM_PROMPT = """You are an intelligence analyst assistant. You are given a scoped \
subgraph (nodes + edges) already selected by a deterministic graph traversal from a \
trigger entity. For each node (except the trigger), assess its relevance to the trigger \
and explain why in one sentence. Return strict JSON:
{"annotations": [{"entity_id": "...", "relevance": "high|medium|low", "rationale": "..."}]}
Do not invent entities or links that are not in the provided subgraph."""


async def explain_scope(trigger_entity_id: str, subgraph: dict) -> dict:
    """Rank/annotate the already-scoped subgraph. Filters the LLM's output to
    only entity_ids that are actually present, so it can't smuggle new nodes in.
    """
    node_ids = {n["entity_id"] for n in subgraph["nodes"]}
    user_prompt = (
        f"trigger_entity_id: {trigger_entity_id}\n"
        f"nodes: {[{'entity_id': n['entity_id'], 'label': n['label'], 'entity_class': n['entity_class']} for n in subgraph['nodes']]}\n"
        f"edges: {[{'source': e['source_entity'], 'target': e['target_entity'], 'link_type': e['link_type']} for e in subgraph['edges']]}"
    )
    raw = await complete_json(SYSTEM_PROMPT, user_prompt)

    annotations = [
        a
        for a in raw.get("annotations", [])
        if a.get("entity_id") in node_ids and a.get("entity_id") != trigger_entity_id
    ]
    return {"trigger_entity_id": trigger_entity_id, "annotations": annotations}
