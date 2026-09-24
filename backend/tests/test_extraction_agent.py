"""Unit test for the extraction agent's ontology-grounding: the prompt must
be built from the graph's real ClassDef/LinkDef vocab, and validation must
reject anything the LLM invents outside that vocab. No real DB or LLM call —
Neo4j and complete_json are both faked/stubbed.
"""
import pytest

from app.services import extraction_agent as extraction_agent_module

VALID_CLASS_KEYS = [
    "PERSON.MILITARY_PERSONNEL",
    "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
]
VALID_LINK_TYPES = ["member_of", "operator_of"]
VALID_LINK_INVERSES = {"operator_of": "operated_by"}
VALID_LINK_DOMAINS = {"member_of": "Person", "operator_of": "Person; Organization"}
VALID_LINK_RANGES = {"member_of": "Organization", "operator_of": "Vehicle; Facility; Equipment"}
VALID_LINK_NOTES = {"operator_of": "Who uses it, as distinct from who owns it"}
EXISTING_ENTITIES = [
    {
        "entity_id": "P-1042",
        "label": "Ivan Petrov",
        "aliases": ["Major Petrov"],
        "entity_class": "PERSON",
    }
]


class FakeRecord(dict):
    """dict subclass so both record["key"] and record.get(...) work, matching
    the neo4j driver's Record interface closely enough for this module."""


class FakeSingleResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class FakeListResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for r in self._records:
            yield r


class FakeSession:
    """Routes each Cypher query to a canned response by distinguishing the
    ontology-vocab list queries from the single-lookup validation queries —
    exactly the two query shapes extraction_agent + ontology.validate issue.
    """

    async def run(self, query, **params):
        if "RETURN c.key AS key" in query:
            return FakeListResult([FakeRecord(key=k) for k in VALID_CLASS_KEYS])
        if "RETURN l.type AS type" in query:
            return FakeListResult(
                [
                    FakeRecord(
                        type=t,
                        domain=VALID_LINK_DOMAINS.get(t, "Any"),
                        range=VALID_LINK_RANGES.get(t, "Any"),
                        notes=VALID_LINK_NOTES.get(t),
                        inverse=VALID_LINK_INVERSES.get(t),
                    )
                    for t in VALID_LINK_TYPES
                ]
            )
        if "MATCH (e:Entity)" in query:
            return FakeListResult([FakeRecord(**e) for e in EXISTING_ENTITIES])
        if "ClassDef {key: $key}) RETURN c LIMIT 1" in query:
            found = params["key"] in VALID_CLASS_KEYS
            return FakeSingleResult(FakeRecord(c="found") if found else None)
        if "VocabValue" in query:
            return FakeSingleResult(FakeRecord(v="found"))  # status always valid here
        if "LinkDef {type: $type}) RETURN l LIMIT 1" in query:
            found = params["type"] in VALID_LINK_TYPES
            record = FakeRecord(l={"domain": "Any", "range": "Any"}) if found else None
            return FakeSingleResult(record)
        if "IngestBatch" in query:
            return FakeSingleResult(None)
        return FakeSingleResult(None)


@pytest.mark.asyncio
async def test_extract_rejects_bogus_terms_and_accepts_real_ones(monkeypatch):
    raw_llm_output = {
        "entities": [
            {
                "entity_id": "E-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",  # real
                "label": "Valid Person",
                "confidence": "B2",
                "source_ref": "D-1",
            },
            {
                "entity_id": "E-2",
                "entity_class": "ORGANIZATION",
                "entity_subclass": "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",  # real
                "label": "Valid Org",
                "confidence": "B2",
                "source_ref": "D-1",
            },
            {
                "entity_id": "E-3",
                "entity_class": "ORGANIZATION",
                "entity_subclass": "ORGANIZATION.MILITARY_UNIT",  # bogus, not in vocab
                "label": "Bogus Org",
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
        "links": [
            {
                "link_id": "L-1",
                "link_type": "member_of",  # real
                "source_entity": "E-1",
                "target_entity": "E-2",
                "confidence": "B2",
                "source_ref": "D-1",
            },
            {
                "link_id": "L-2",
                "link_type": "REPORTS_TO",  # bogus, not in vocab
                "source_entity": "E-1",
                "target_entity": "E-2",
                "confidence": "B2",
                "source_ref": "D-1",
            },
            {
                "link_id": "L-3",
                "link_type": "member_of",
                "source_entity": "E-1",
                "target_entity": "E-3",  # references the rejected bogus entity
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
    }

    captured_prompt = {}

    async def fake_complete_json(system_prompt, user_prompt):
        captured_prompt["system"] = system_prompt
        return raw_llm_output

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    batch = await extraction_agent_module.extract_from_text(session, "some scenario text")

    # the prompt was grounded in the real (fake-DB) vocab, not hardcoded
    assert "PERSON.MILITARY_PERSONNEL" in captured_prompt["system"]
    assert "member_of" in captured_prompt["system"]

    valid_entity_ids = {e["entity_id"] for e in batch["entities"]}
    assert valid_entity_ids == {"E-1", "E-2"}

    assert len(batch["rejected_entities"]) == 1
    assert batch["rejected_entities"][0]["row"]["entity_subclass"] == "ORGANIZATION.MILITARY_UNIT"
    assert "not a known ClassDef" in batch["rejected_entities"][0]["reason"]

    valid_link_ids = {l["link_id"] for l in batch["links"]}
    assert valid_link_ids == {"L-1"}

    assert len(batch["rejected_links"]) == 2
    reasons = {r["row"]["link_id"]: r["reason"] for r in batch["rejected_links"]}
    assert "not a known LinkDef" in reasons["L-2"]
    assert "not in this batch's valid set" in reasons["L-3"]


@pytest.mark.asyncio
async def test_extract_normalizes_inverse_link_type_and_swaps_endpoints(monkeypatch):
    """Regression test: the LLM emitting a documented inverse name (e.g.
    'operated_by', the inverse of 'operator_of') must not be rejected — it
    should be normalized to the canonical forward type with source/target
    swapped, since 81% of real link types have a documented inverse."""
    raw_llm_output = {
        "entities": [
            {
                "entity_id": "E-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "Facility",
                "confidence": "B2",
                "source_ref": "D-1",
            },
            {
                "entity_id": "E-2",
                "entity_class": "ORGANIZATION",
                "entity_subclass": "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
                "label": "Regiment",
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
        "links": [
            {
                "link_id": "L-1",
                "link_type": "operated_by",  # inverse phrasing, not a LinkDef itself
                "source_entity": "E-1",  # the facility in the text
                "target_entity": "E-2",  # the operating organization in the text
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
    }

    async def fake_complete_json(system_prompt, user_prompt):
        return raw_llm_output

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    batch = await extraction_agent_module.extract_from_text(session, "some scenario text")

    assert len(batch["rejected_links"]) == 0
    assert len(batch["links"]) == 1
    link = batch["links"][0]
    assert link["link_type"] == "operator_of"
    # source/target swapped: the org (operator) is now the source
    assert link["source_entity"] == "E-2"
    assert link["target_entity"] == "E-1"


@pytest.mark.asyncio
async def test_extract_reuses_existing_entity_instead_of_duplicating(monkeypatch):
    """Regression test: when a follow-up scenario re-mentions an entity the
    model already knows about (echoed back with its real, pre-existing
    entity_id — the seam this feature adds to the prompt), a link may target
    it directly without that entity_id appearing in this batch's "entities"
    array at all, and it must not be rejected as an unknown reference."""
    raw_llm_output = {
        "entities": [
            {
                "entity_id": "O-1",
                "entity_class": "ORGANIZATION",
                "entity_subclass": "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
                "label": "New Unit",
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
        "links": [
            {
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-1042",  # reused from EXISTING_ENTITIES, not re-declared
                "target_entity": "O-1",
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
    }

    async def fake_complete_json(system_prompt, user_prompt):
        return raw_llm_output

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    batch = await extraction_agent_module.extract_from_text(session, "some scenario text")

    assert len(batch["rejected_links"]) == 0
    assert len(batch["links"]) == 1
    assert batch["links"][0]["source_entity"] == "P-1042"
    # only the genuinely new entity is in the batch — the reused one isn't duplicated
    assert {e["entity_id"] for e in batch["entities"]} == {"O-1"}


@pytest.mark.asyncio
async def test_extract_and_validate_folds_extra_existing_entities_into_prompt_and_validation(
    monkeypatch,
):
    """The cross-chunk dedup hook: entities accepted in an earlier chunk of
    the same document must both appear in the prompt's EXISTING_ENTITIES
    section and be valid link targets, without being re-declared."""
    extra_entities = [
        {"entity_id": "P-99", "label": "Chunk-1 Person", "aliases": [], "entity_class": "PERSON"}
    ]
    raw_llm_output = {
        "entities": [],
        "links": [
            {
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-99",  # only valid because of extra_existing_entities
                "target_entity": "P-1042",  # from the graph's EXISTING_ENTITIES
                "confidence": "B2",
                "source_ref": "D-1",
            },
        ],
    }
    captured_prompt = {}

    async def fake_complete_json(system_prompt, user_prompt):
        captured_prompt["system"] = system_prompt
        return raw_llm_output

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    result = await extraction_agent_module._extract_and_validate(
        session, "chunk 2 text", extra_existing_entities=extra_entities
    )

    assert "P-99" in captured_prompt["system"]
    assert result["rejected_links"] == []
    assert len(result["valid_links"]) == 1
    assert result["valid_links"][0]["source_entity"] == "P-99"


@pytest.mark.asyncio
async def test_classify_rejected_entities_returns_valid_leaf_keys(monkeypatch):
    rejected = [
        {
            "row": {
                "label": "Ben Bernanke",
                "entity_class": "Person",
                "entity_subclass": "Person.Individual",
            },
            "reason": "entity_subclass 'Person.Individual' is not a known ClassDef key",
        },
        {
            "row": {
                "label": "Bogus Thing",
                "entity_class": "Nonsense",
                "entity_subclass": "Nonsense.Thing",
            },
            "reason": "entity_subclass 'Nonsense.Thing' is not a known ClassDef key",
        },
    ]

    async def fake_complete_json(system_prompt, user_prompt):
        assert "Ben Bernanke" in system_prompt
        assert "PERSON.MILITARY_PERSONNEL" in system_prompt
        return {
            "classifications": [
                {"idx": 0, "entity_subclass": "PERSON.MILITARY_PERSONNEL"},
                # row 1: the model correctly declines rather than guessing
            ]
        }

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    result = await extraction_agent_module.classify_rejected_entities(session, rejected)

    assert result == [
        {"idx": 0, "entity_subclass": "PERSON.MILITARY_PERSONNEL", "entity_class": "PERSON"}
    ]


@pytest.mark.asyncio
async def test_classify_rejected_entities_ignores_hallucinated_key_and_index(monkeypatch):
    rejected = [
        {
            "row": {"label": "Ben Bernanke", "entity_class": "Person", "entity_subclass": "Person.Individual"},
            "reason": "entity_subclass 'Person.Individual' is not a known ClassDef key",
        },
    ]

    async def fake_complete_json(system_prompt, user_prompt):
        return {
            "classifications": [
                {"idx": 0, "entity_subclass": "MADE_UP.NOT_REAL"},  # not a real key
                {"idx": 7, "entity_subclass": "PERSON.MILITARY_PERSONNEL"},  # out-of-range index
            ]
        }

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    result = await extraction_agent_module.classify_rejected_entities(session, rejected)

    assert result == []


@pytest.mark.asyncio
async def test_classify_rejected_entities_splits_large_batches_into_chunks(monkeypatch):
    """A batch bigger than _CLASSIFY_CHUNK_SIZE must become multiple LLM
    calls, each scoped to its own slice of rows with globally-correct idx
    values — the regression this guards is a single giant prompt covering
    the whole batch, which degrades as row count grows."""
    monkeypatch.setattr(extraction_agent_module, "_CLASSIFY_CHUNK_SIZE", 2)

    rejected = [
        {"row": {"label": f"Entity {i}", "entity_class": "Person", "entity_subclass": "Person.Individual"}, "reason": "x"}
        for i in range(5)
    ]

    calls = []
    # 5 rows at chunk size 2 -> chunks start at 0, 2, 4; each call classifies
    # only its chunk's first row, to prove idx values are global (not reset
    # to 0 per chunk).
    chunk_starts = [0, 2, 4]

    async def fake_complete_json(system_prompt, user_prompt):
        calls.append(system_prompt)
        start = chunk_starts[len(calls) - 1]
        return {"classifications": [{"idx": start, "entity_subclass": "PERSON.MILITARY_PERSONNEL"}]}

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    result = await extraction_agent_module.classify_rejected_entities(session, rejected)

    assert len(calls) == 3
    assert {r["idx"] for r in result} == {0, 2, 4}
    assert "Entity 0" in calls[0] and "Entity 1" in calls[0] and "Entity 2" not in calls[0]
    assert "Entity 4" in calls[2] and "Entity 3" not in calls[2]


@pytest.mark.asyncio
async def test_classify_rejected_entities_skips_llm_call_when_nothing_to_classify(monkeypatch):
    called = False

    async def fake_complete_json(system_prompt, user_prompt):
        nonlocal called
        called = True
        return {"classifications": []}

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    result = await extraction_agent_module.classify_rejected_entities(session, [])

    assert result == []
    assert called is False


@pytest.mark.asyncio
async def test_classify_rejected_entities_propagates_llm_error(monkeypatch):
    from app.llm.client import LLMError

    rejected = [
        {
            "row": {"label": "Ben Bernanke", "entity_class": "Person", "entity_subclass": "Person.Individual"},
            "reason": "entity_subclass 'Person.Individual' is not a known ClassDef key",
        },
    ]

    async def fake_complete_json(system_prompt, user_prompt):
        raise LLMError("boom")

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    session = FakeSession()
    with pytest.raises(LLMError):
        await extraction_agent_module.classify_rejected_entities(session, rejected)
