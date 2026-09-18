"""API-level tests for /documents endpoints. Neo4j is faked (no real DB
needed); background extraction itself is stubbed out here (covered by
test_document_ingest.py) — this file only checks the HTTP contract:
validation, status codes, response shape, and that a valid upload
schedules background processing for the right document_id.
"""
import io

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


class _ListResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for r in self._records:
            yield {"d": r}


class _RecordingSession:
    def __init__(self, records=None):
        self.records = records or []
        self.run_calls = []

    async def run(self, query, **params):
        self.run_calls.append((query, params))
        if "MATCH (d:Document) RETURN d" in query:
            return _ListResult(self.records)
        if "MATCH (d:Document {document_id: $id}) RETURN d" in query:
            match = next((r for r in self.records if r["document_id"] == params["id"]), None)
            return _SingleResult({"d": match} if match else None)
        return _SingleResult(None)


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_extension(monkeypatch):
    from app.api import documents as documents_module

    session = _RecordingSession()
    monkeypatch.setattr(documents_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/documents/upload",
            files={"file": ("notes.csv", io.BytesIO(b"a,b,c"), "text/csv")},
        )
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"]
    assert session.run_calls == []  # save_upload rejected before touching the DB


@pytest.mark.asyncio
async def test_upload_accepts_valid_file_and_schedules_processing(monkeypatch, tmp_path):
    from app.api import documents as documents_module
    from app.config import Settings
    from app.services import document_ingest as document_ingest_module

    session = _RecordingSession()
    monkeypatch.setattr(documents_module, "get_driver", lambda: _FakeDriver(session))
    monkeypatch.setattr(
        document_ingest_module,
        "get_settings",
        lambda: Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=8000),
    )

    scheduled = []

    async def fake_process_document(document_id):
        scheduled.append(document_id)

    monkeypatch.setattr(documents_module, "process_document", fake_process_document)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/documents/upload",
            files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processing"
    assert body["filename"] == "notes.txt"
    assert body["file_type"] == "txt"
    assert scheduled == [body["document_id"]]
    assert (tmp_path / body["document_id"] / "notes.txt").read_bytes() == b"hello world"


@pytest.mark.asyncio
async def test_list_documents_orders_newest_first(monkeypatch):
    from app.api import documents as documents_module

    records = [
        {
            "document_id": "D-1",
            "filename": "a.txt",
            "file_type": "txt",
            "size_bytes": 1,
            "file_path": "D-1/a.txt",
            "status": "proposed",
            "error_message": None,
            "batch_id": "B-1",
            "uploaded_at": "2026-09-18T01:00:00+00:00",
        }
    ]
    session = _RecordingSession(records=records)
    monkeypatch.setattr(documents_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/documents")

    assert resp.status_code == 200
    assert [d["document_id"] for d in resp.json()] == ["D-1"]
    assert "ORDER BY d.uploaded_at DESC" in session.run_calls[0][0]


@pytest.mark.asyncio
async def test_get_document_404s_when_missing(monkeypatch):
    from app.api import documents as documents_module

    session = _RecordingSession(records=[])
    monkeypatch.setattr(documents_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/documents/D-missing")

    assert resp.status_code == 404
