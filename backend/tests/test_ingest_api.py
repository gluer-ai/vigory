"""API-level tests for the /ingest batch endpoints added for the
knowledge-base upload flow: GET /ingest/{batch_id} (fetch a
background-produced batch for review) and the document_id side effect on
commit. Neo4j is faked; extraction itself is covered by
test_extraction_agent.py.
"""
import json

import httpx
import pytest

from app.main import create_app


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return _FakeSessionCtx(self._session)


class _SingleResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class _FakeSession:
    """A single canned IngestBatch (and, once committed, a Document to
    flip), plus enough ontology/entity surface for validate_entity/
    validate_link and the manual add-entity/add-link/suggest endpoints."""

    def __init__(
        self,
        batch,
        document=None,
        valid_class_keys=("PERSON.MILITARY_PERSONNEL", "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR"),
        class_roots=None,
        valid_link_defs=None,
        committed_entities=None,
        committed_links=None,
    ):
        self.batch = batch
        self.document = document
        self.run_calls = []
        self.valid_class_keys = set(valid_class_keys)
        self.class_roots = class_roots or {
            "PERSON": "Person",
            "ORGANIZATION": "Organization",
            "VEHICLE": "Vehicle",
        }
        self.valid_link_defs = valid_link_defs or {
            "member_of": {"domain": "Person", "range": "Organization"},
        }
        self.committed_entities = committed_entities or {}
        self.committed_links = committed_links or {}

    async def run(self, query, **params):
        self.run_calls.append((query, params))
        if "MATCH (b:IngestBatch {batch_id: $id}) RETURN b" in query:
            match = self.batch if params["id"] == self.batch["batch_id"] else None
            return _SingleResult({"b": match} if match else None)
        if "SET b.status = 'committed'" in query:
            self.batch["status"] = "committed"
            return _SingleResult(None)
        if "SET b.entities = $entities" in query:
            self.batch["entities"] = params["entities"]
            return _SingleResult(None)
        if "SET b.links = $links" in query:
            self.batch["links"] = params["links"]
            return _SingleResult(None)
        if "SET d.status = 'committed'" in query:
            if self.document is not None:
                self.document["status"] = "committed"
            return _SingleResult(None)
        if "ClassDef {key: $key}) RETURN c LIMIT 1" in query:
            found = params["key"] in self.valid_class_keys
            return _SingleResult({"c": "found"} if found else None)
        if "root_label" in query:
            root = self.class_roots.get(params["key"])
            return _SingleResult({"root_label": root} if root else None)
        if "VocabValue" in query:
            return _SingleResult({"v": "found"})
        if "LinkDef {type: $type}) RETURN l LIMIT 1" in query:
            linkdef = self.valid_link_defs.get(params["type"])
            return _SingleResult({"l": linkdef} if linkdef else None)
        if "[r:LINK {link_id: $id}]" in query:
            match = self.committed_links.get(params["id"])
            return _SingleResult({"r": match} if match else None)
        if "MATCH (e:Entity {entity_id: $id}) RETURN e" in query:
            match = self.committed_entities.get(params["id"])
            return _SingleResult({"e": match} if match else None)
        # MERGE (n:Entity ...) / MERGE (s)-[r:LINK ...] — recorded, not read
        return _SingleResult(None)


def _make_batch(**overrides):
    batch = {
        "batch_id": "B-1",
        "status": "proposed",
        "source_text": "some text",
        "entities": json.dumps([]),
        "links": json.dumps([]),
        "rejected_entities": json.dumps([{"row": {}, "reason": "bad"}]),
        "rejected_links": json.dumps([]),
    }
    batch.update(overrides)
    return batch


@pytest.mark.asyncio
async def test_get_batch_returns_entities_links_and_rejected(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ingest/B-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == "B-1"
    assert body["rejected_entities"] == [{"row": {}, "reason": "bad"}]


@pytest.mark.asyncio
async def test_get_batch_404s_when_missing(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ingest/B-missing")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_commit_batch_also_flips_linked_document_to_committed(monkeypatch):
    from app.api import ingest as ingest_module

    document = {"document_id": "D-1", "status": "proposed"}
    session = _FakeSession(batch=_make_batch(document_id="D-1"), document=document)
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/B-1/commit")

    assert resp.status_code == 200
    assert document["status"] == "committed"


@pytest.mark.asyncio
async def test_commit_batch_without_document_id_does_not_touch_document(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())  # no document_id — paste-text batch
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/B-1/commit")

    assert resp.status_code == 200
    assert not any("SET d.status" in call[0] for call in session.run_calls)


@pytest.mark.asyncio
async def test_add_batch_entity_happy_path(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "Ivan Petrov",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entities"]) == 1
    assert body["entities"][0]["entity_id"] == "P-1"
    assert body["entities"][0]["label"] == "Ivan Petrov"


@pytest.mark.asyncio
async def test_add_batch_entity_404s_when_batch_missing(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-missing/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "X",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_batch_entity_409s_when_batch_not_proposed(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch(status="committed"))
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "X",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_batch_entity_409s_on_id_collision_within_batch(monkeypatch):
    from app.api import ingest as ingest_module

    existing_entity = {
        "entity_id": "P-1",
        "entity_class": "PERSON",
        "entity_subclass": "PERSON.MILITARY_PERSONNEL",
        "label": "Already here",
        "aliases": [],
        "status": "active",
        "confidence": "B2",
        "source_ref": "manual",
        "first_observed": None,
        "last_observed": None,
        "attrs": {},
    }
    session = _FakeSession(batch=_make_batch(entities=json.dumps([existing_entity])))
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "New one",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_batch_entity_409s_on_id_collision_with_committed_entity(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(
        batch=_make_batch(),
        committed_entities={"P-1": {"entity_id": "P-1", "label": "Already committed"}},
    )
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                "label": "New one",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_batch_entity_422s_on_invalid_subclass(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/entities",
            json={
                "entity_id": "P-1",
                "entity_class": "PERSON",
                "entity_subclass": "PERSON.MADE_UP_SUBCLASS",
                "label": "X",
                "aliases": [],
                "status": "active",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 422
    assert "not a known ClassDef" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_suggest_entity_returns_match(monkeypatch):
    from app.api import ingest as ingest_module

    async def fake_find_synonym_match(session, label, entity_class, aliases, batch_entities):
        assert label == "Automobile"
        assert entity_class == "VEHICLE"
        return {
            "entity_id": "V-1",
            "label": "Car",
            "aliases": [],
            "entity_class": "VEHICLE",
            "entity_subclass": "VEHICLE.CIVILIAN_VEHICLE.CIVILIAN_CAR",
            "reason": "Automobile is a synonym of Car",
        }

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))
    monkeypatch.setattr(ingest_module, "find_synonym_match", fake_find_synonym_match)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/ingest/B-1/entities/suggest",
            params={"label": "Automobile", "entity_class": "VEHICLE"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["match"]["entity_id"] == "V-1"
    assert body["reason"] == "Automobile is a synonym of Car"


@pytest.mark.asyncio
async def test_suggest_entity_returns_null_match_when_none_found(monkeypatch):
    from app.api import ingest as ingest_module

    async def fake_find_synonym_match(session, label, entity_class, aliases, batch_entities):
        return None

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))
    monkeypatch.setattr(ingest_module, "find_synonym_match", fake_find_synonym_match)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/ingest/B-1/entities/suggest",
            params={"label": "Something new", "entity_class": "PERSON"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"match": None, "reason": None}


@pytest.mark.asyncio
async def test_suggest_entity_502s_on_llm_error(monkeypatch):
    from app.api import ingest as ingest_module
    from app.llm.client import LLMError

    async def fake_find_synonym_match(session, label, entity_class, aliases, batch_entities):
        raise LLMError("boom")

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))
    monkeypatch.setattr(ingest_module, "find_synonym_match", fake_find_synonym_match)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/ingest/B-1/entities/suggest",
            params={"label": "Automobile", "entity_class": "VEHICLE"},
        )

    assert resp.status_code == 502
