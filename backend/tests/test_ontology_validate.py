"""Pure-Python unit tests for ontology validation — no DB, using a fake session."""
import pytest

from app.models.entity import EntityBase
from app.models.link import LinkBase
from app.ontology import validate as validate_module
from app.ontology.validate import ValidationError, validate_entity, validate_link


class FakeResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class FakeSession:
    """Minimal stand-in for neo4j.AsyncSession, driven by a lookup table keyed
    by a substring of the Cypher query so each test wires only what it needs.
    """

    def __init__(self, responses: dict):
        self.responses = responses

    async def run(self, query, **params):
        for key, record in self.responses.items():
            if key in query:
                return FakeResult(record)
        return FakeResult(None)


@pytest.mark.asyncio
async def test_validate_entity_unknown_subclass_rejected():
    session = FakeSession({"ClassDef": None})
    entity = EntityBase(
        entity_class="PERSON",
        entity_subclass="PERSON.NOT_REAL",
        label="Test",
        confidence="B2",
        source_ref="D-1",
    )
    with pytest.raises(ValidationError, match="not a known ClassDef"):
        await validate_entity(session, entity)


@pytest.mark.asyncio
async def test_validate_entity_known_subclass_and_status_passes():
    session = FakeSession({"ClassDef": {"c": "found"}, "VocabValue": {"v": "found"}})
    entity = EntityBase(
        entity_class="PERSON",
        entity_subclass="PERSON.MILITARY_PERSONNEL",
        label="Test",
        status="active",
        confidence="B2",
        source_ref="D-1",
    )
    await validate_entity(session, entity)  # no raise


@pytest.mark.asyncio
async def test_validate_entity_bad_status_rejected():
    session = FakeSession({"ClassDef": {"c": "found"}, "VocabValue": None})
    entity = EntityBase(
        entity_class="PERSON",
        entity_subclass="PERSON.MILITARY_PERSONNEL",
        label="Test",
        status="active",
        confidence="B2",
        source_ref="D-1",
    )
    with pytest.raises(ValidationError, match="entity_status vocabulary"):
        await validate_entity(session, entity)


@pytest.mark.asyncio
async def test_validate_link_unknown_type_rejected():
    session = FakeSession({"LinkDef": None})
    link = LinkBase(
        link_type="not_a_real_link",
        source_entity="P-1",
        target_entity="P-2",
        confidence="B2",
        source_ref="D-1",
    )
    with pytest.raises(ValidationError, match="not a known LinkDef"):
        await validate_link(session, link, "PERSON.X", "PERSON.Y")


@pytest.mark.asyncio
async def test_validate_link_domain_mismatch_rejected(monkeypatch):
    session = FakeSession(
        {"LinkDef": {"l": {"domain": "Person", "range": "Organization"}}}
    )

    async def fake_root(session, key):
        return "Vehicle" if "PERSON" not in key else "Person"

    monkeypatch.setattr(validate_module, "_class_root", fake_root)

    link = LinkBase(
        link_type="member_of",
        source_entity="V-1",
        target_entity="O-1",
        confidence="B2",
        source_ref="D-1",
    )
    with pytest.raises(ValidationError, match="requires domain 'Person'"):
        await validate_link(session, link, "VEHICLE.X", "ORGANIZATION.Y")


@pytest.mark.asyncio
async def test_validate_link_accepts_any_class_in_multi_value_domain(monkeypatch):
    """Regression test: domain/range columns can be a '; '-separated list
    (e.g. 'Organization; Vehicle') and must be treated as membership, not
    compared as one literal string — see based_at in the real ontology."""
    session = FakeSession(
        {"LinkDef": {"l": {"domain": "Organization; Vehicle", "range": "Facility"}}}
    )

    async def fake_root(session, key):
        return "Organization" if "ORG" in key else "Facility"

    monkeypatch.setattr(validate_module, "_class_root", fake_root)

    link = LinkBase(
        link_type="based_at",
        source_entity="O-1",
        target_entity="F-1",
        confidence="B2",
        source_ref="D-1",
    )
    await validate_link(session, link, "ORGANIZATION.X", "FACILITY.Y")  # no raise
