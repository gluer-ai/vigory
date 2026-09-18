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
    flip) — enough surface for get_batch/commit_batch's queries."""

    def __init__(self, batch, document=None):
        self.batch = batch
        self.document = document
        self.run_calls = []

    async def run(self, query, **params):
        self.run_calls.append((query, params))
        if "MATCH (b:IngestBatch {batch_id: $id}) RETURN b" in query:
            match = self.batch if params["id"] == self.batch["batch_id"] else None
            return _SingleResult({"b": match} if match else None)
        if "SET b.status = 'committed'" in query:
            self.batch["status"] = "committed"
            return _SingleResult(None)
        if "SET d.status = 'committed'" in query:
            if self.document is not None:
                self.document["status"] = "committed"
            return _SingleResult(None)
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
