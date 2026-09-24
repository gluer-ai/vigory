"""Unit tests for find_synonym_match: the LLM-backed duplicate/synonym
check run before a manually-created entity is added to a batch. Neo4j and
complete_json are both faked, same approach as test_extraction_agent.py.
"""
import pytest

from app.services import entity_resolution as entity_resolution_module


class FakeRecord(dict):
    pass


class FakeListResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for r in self._records:
            yield r


class FakeSession:
    """Routes the one candidate-lookup query this module issues, filtered
    by entity_class like the real Cypher does."""

    def __init__(self, entities: list[dict]):
        self.entities = entities

    async def run(self, query, **params):
        assert "MATCH (e:Entity)" in query
        matches = [e for e in self.entities if e["entity_class"] == params["entity_class"]]
        return FakeListResult(
            [
                FakeRecord(
                    entity_id=e["entity_id"],
                    label=e["label"],
                    aliases=e.get("aliases", []),
                    entity_subclass=e.get("entity_subclass", ""),
                )
                for e in matches
            ]
        )


@pytest.mark.asyncio
async def test_no_candidates_skips_llm_call(monkeypatch):
    called = False

    async def fake_complete_json(system_prompt, user_prompt):
        nonlocal called
        called = True
        return {"match_entity_id": None, "reason": ""}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    session = FakeSession(entities=[])
    match = await entity_resolution_module.find_synonym_match(
        session, label="Automobile", entity_class="VEHICLE", aliases=[], batch_entities=[]
    )

    assert match is None
    assert called is False


@pytest.mark.asyncio
async def test_returns_matched_candidate(monkeypatch):
    session = FakeSession(
        entities=[
            {
                "entity_id": "V-1",
                "label": "Car",
                "aliases": [],
                "entity_class": "VEHICLE",
                "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
            },
        ]
    )

    async def fake_complete_json(system_prompt, user_prompt):
        assert "V-1 | Car" in system_prompt
        return {"match_entity_id": "V-1", "reason": "Automobile is a synonym of Car"}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    match = await entity_resolution_module.find_synonym_match(
        session, label="Automobile", entity_class="VEHICLE", aliases=[], batch_entities=[]
    )

    assert match == {
        "entity_id": "V-1",
        "label": "Car",
        "aliases": [],
        "entity_class": "VEHICLE",
        "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
        "reason": "Automobile is a synonym of Car",
    }


@pytest.mark.asyncio
async def test_batch_entities_of_same_class_are_included_as_candidates(monkeypatch):
    session = FakeSession(entities=[])
    batch_entities = [
        {
            "entity_id": "V-2",
            "label": "Automobile",
            "aliases": [],
            "entity_class": "VEHICLE",
            "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
        }
    ]

    captured = {}

    async def fake_complete_json(system_prompt, user_prompt):
        captured["system"] = system_prompt
        return {"match_entity_id": None, "reason": ""}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    await entity_resolution_module.find_synonym_match(
        session, label="Car", entity_class="VEHICLE", aliases=[], batch_entities=batch_entities
    )

    assert "V-2" in captured["system"]


@pytest.mark.asyncio
async def test_batch_entities_of_a_different_class_are_excluded(monkeypatch):
    session = FakeSession(entities=[])
    batch_entities = [
        {"entity_id": "P-1", "label": "Someone", "aliases": [], "entity_class": "PERSON"}
    ]
    called = False

    async def fake_complete_json(system_prompt, user_prompt):
        nonlocal called
        called = True
        return {"match_entity_id": None, "reason": ""}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    match = await entity_resolution_module.find_synonym_match(
        session, label="Car", entity_class="VEHICLE", aliases=[], batch_entities=batch_entities
    )

    assert match is None
    assert called is False


@pytest.mark.asyncio
async def test_hallucinated_match_id_outside_candidates_is_ignored(monkeypatch):
    session = FakeSession(
        entities=[
            {
                "entity_id": "V-1",
                "label": "Car",
                "aliases": [],
                "entity_class": "VEHICLE",
                "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
            }
        ]
    )

    async def fake_complete_json(system_prompt, user_prompt):
        return {"match_entity_id": "V-999", "reason": "hallucinated"}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    match = await entity_resolution_module.find_synonym_match(
        session, label="Automobile", entity_class="VEHICLE", aliases=[], batch_entities=[]
    )

    assert match is None


@pytest.mark.asyncio
async def test_llm_error_propagates(monkeypatch):
    from app.llm.client import LLMError

    session = FakeSession(
        entities=[
            {
                "entity_id": "V-1",
                "label": "Car",
                "aliases": [],
                "entity_class": "VEHICLE",
                "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
            }
        ]
    )

    async def fake_complete_json(system_prompt, user_prompt):
        raise LLMError("boom")

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    with pytest.raises(LLMError):
        await entity_resolution_module.find_synonym_match(
            session, label="Automobile", entity_class="VEHICLE", aliases=[], batch_entities=[]
        )


@pytest.mark.asyncio
async def test_candidate_with_none_aliases_is_normalized_to_empty_list(monkeypatch):
    """Test that a Neo4j entity with no aliases property (None) is normalized
    to an empty list when returned as a match. This simulates entities that
    were created or imported without going through this app's entity creation
    paths."""
    session = FakeSession(
        entities=[
            {
                "entity_id": "V-1",
                "label": "Car",
                "aliases": None,
                "entity_class": "VEHICLE",
                "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
            },
        ]
    )

    async def fake_complete_json(system_prompt, user_prompt):
        assert "V-1 | Car" in system_prompt
        return {"match_entity_id": "V-1", "reason": "Automobile is a synonym of Car"}

    monkeypatch.setattr(entity_resolution_module, "complete_json", fake_complete_json)

    match = await entity_resolution_module.find_synonym_match(
        session, label="Automobile", entity_class="VEHICLE", aliases=[], batch_entities=[]
    )

    assert match == {
        "entity_id": "V-1",
        "label": "Car",
        "aliases": [],
        "entity_class": "VEHICLE",
        "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
        "reason": "Automobile is a synonym of Car",
    }
