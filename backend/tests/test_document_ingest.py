"""Tests for save_upload/process_document: the document-upload counterpart
to test_extraction_agent.py — disk I/O, chunk-by-chunk extraction,
cross-chunk entity dedup, and the merge into a single IngestBatch. Neo4j
and the LLM are both faked, same approach as test_extraction_agent.py.
"""
import json

import pytest

from app.config import Settings
from app.services import document_ingest as document_ingest_module
from app.services import extraction_agent as extraction_agent_module

VALID_CLASS_KEYS = [
    "PERSON.MILITARY_PERSONNEL",
    "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
]
VALID_LINK_TYPES = ["member_of"]


class FakeRecord(dict):
    pass


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
    """Routes ontology-vocab queries to canned vocab, Document
    lookups/updates to an in-memory dict, and records the IngestBatch it's
    asked to create so the test can assert on the merged result."""

    def __init__(self, document: dict):
        self.document = document
        self.created_batch: dict | None = None

    async def run(self, query, **params):
        if "RETURN c.key AS key" in query:
            return FakeListResult([FakeRecord(key=k) for k in VALID_CLASS_KEYS])
        if "RETURN l.type AS type" in query:
            return FakeListResult(
                [
                    FakeRecord(type=t, domain="Any", range="Any", notes=None, inverse=None)
                    for t in VALID_LINK_TYPES
                ]
            )
        if "MATCH (e:Entity)" in query:
            return FakeListResult([])
        if "ClassDef {key: $key}) RETURN c LIMIT 1" in query:
            found = params["key"] in VALID_CLASS_KEYS
            return FakeSingleResult(FakeRecord(c="found") if found else None)
        if "VocabValue" in query:
            return FakeSingleResult(FakeRecord(v="found"))
        if "LinkDef {type: $type}) RETURN l LIMIT 1" in query:
            found = params["type"] in VALID_LINK_TYPES
            record = FakeRecord(l={"domain": "Any", "range": "Any"}) if found else None
            return FakeSingleResult(record)
        if "CREATE (b:IngestBatch" in query:
            self.created_batch = params
            return FakeSingleResult(None)
        if "MATCH (d:Document {document_id: $id}) RETURN d" in query:
            match = self.document if params["id"] == self.document["document_id"] else None
            return FakeSingleResult(FakeRecord(d=dict(match)) if match else None)
        if "SET d.status" in query:
            self.document.update({k: v for k, v in params.items() if k != "id"})
            return FakeSingleResult(None)
        return FakeSingleResult(None)


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


@pytest.mark.asyncio
async def test_save_upload_writes_file_and_creates_processing_document(tmp_path, monkeypatch):
    monkeypatch.setattr(
        document_ingest_module,
        "get_settings",
        lambda: Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=8000),
    )
    session = FakeSession(document={})
    doc = await document_ingest_module.save_upload(session, "notes.txt", b"hello world")

    assert doc["status"] == "processing"
    assert doc["file_type"] == "txt"
    assert doc["size_bytes"] == len(b"hello world")
    assert doc["batch_id"] is None
    written = tmp_path / doc["document_id"] / "notes.txt"
    assert written.read_bytes() == b"hello world"


@pytest.mark.asyncio
async def test_save_upload_strips_path_components_from_filename(tmp_path, monkeypatch):
    """A malicious/unexpected filename like '../../etc/passwd' must not
    escape the document's upload directory."""
    monkeypatch.setattr(
        document_ingest_module,
        "get_settings",
        lambda: Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=8000),
    )
    session = FakeSession(document={})
    doc = await document_ingest_module.save_upload(session, "../../etc/passwd.txt", b"data")

    assert doc["filename"] == "passwd.txt"
    assert (tmp_path / doc["document_id"] / "passwd.txt").read_bytes() == b"data"
    assert not (tmp_path.parent.parent / "etc" / "passwd.txt").exists()


@pytest.mark.asyncio
async def test_save_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(
        document_ingest_module,
        "get_settings",
        lambda: Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=8000),
    )
    session = FakeSession(document={})
    with pytest.raises(ValueError, match="unsupported"):
        await document_ingest_module.save_upload(session, "notes.csv", b"a,b,c")


@pytest.mark.asyncio
async def test_save_upload_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        document_ingest_module,
        "get_settings",
        lambda: Settings(upload_dir=str(tmp_path), max_upload_mb=0, ingest_chunk_chars=8000),
    )
    session = FakeSession(document={})
    with pytest.raises(ValueError, match="exceeds"):
        await document_ingest_module.save_upload(session, "notes.txt", b"hello world")


@pytest.mark.asyncio
async def test_process_document_merges_chunks_into_one_batch_with_cross_chunk_dedup(
    tmp_path, monkeypatch
):
    settings = Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=30)
    monkeypatch.setattr(document_ingest_module, "get_settings", lambda: settings)

    document_id = "D-abc12345"
    doc_dir = tmp_path / document_id
    doc_dir.mkdir()
    # Two paragraphs (30 and 26 chars) with ingest_chunk_chars=30: each fits
    # alone but not together, so chunk_text produces exactly two chunks —
    # one _extract_and_validate call per paragraph below.
    (doc_dir / "notes.txt").write_text(
        "Ivan Petrov commands the unit.\n\nIvan Petrov reports to HQ."
    )
    document = {
        "document_id": document_id,
        "filename": "notes.txt",
        "file_type": "txt",
        "size_bytes": 100,
        "file_path": f"{document_id}/notes.txt",
        "status": "processing",
        "error_message": None,
        "batch_id": None,
        "uploaded_at": "2026-09-18T00:00:00+00:00",
    }
    session = FakeSession(document=document)
    monkeypatch.setattr(document_ingest_module, "get_driver", lambda: _FakeDriver(session))

    chunk_calls = []

    async def fake_complete_json(system_prompt, user_prompt):
        chunk_calls.append(user_prompt)
        if len(chunk_calls) == 1:
            return {
                "entities": [
                    {
                        "entity_id": "P-1",
                        "entity_class": "PERSON",
                        "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                        "label": "Ivan Petrov",
                        "confidence": "B2",
                        "source_ref": "notes.txt",
                    }
                ],
                "links": [],
            }
        # Second chunk correctly reuses P-1 instead of re-declaring it,
        # because it was folded into this chunk's existing-entities context.
        return {
            "entities": [
                {
                    "entity_id": "O-1",
                    "entity_class": "ORGANIZATION",
                    "entity_subclass": "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
                    "label": "HQ",
                    "confidence": "B2",
                    "source_ref": "notes.txt",
                }
            ],
            "links": [
                {
                    "link_id": "L-1",
                    "link_type": "member_of",
                    "source_entity": "P-1",
                    "target_entity": "O-1",
                    "confidence": "B2",
                    "source_ref": "notes.txt",
                }
            ],
        }

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    await document_ingest_module.process_document(document_id)

    assert len(chunk_calls) == 2
    assert session.created_batch is not None
    entities = json.loads(session.created_batch["entities"])
    links = json.loads(session.created_batch["links"])
    assert {e["entity_id"] for e in entities} == {"P-1", "O-1"}
    assert len(entities) == 2  # no duplicate O-1/P-1 rows
    assert len(links) == 1
    assert links[0]["source_entity"] == "P-1"
    assert links[0]["target_entity"] == "O-1"
    assert session.created_batch["document_id"] == document_id

    assert session.document["status"] == "proposed"
    assert session.document["batch_id"] == session.created_batch["batch_id"]


@pytest.mark.asyncio
async def test_process_document_renames_colliding_new_entity_ids_across_chunks(
    tmp_path, monkeypatch
):
    """Two independent chunks can coincidentally mint the SAME entity_id for
    two genuinely different new entities (e.g. both start their own local
    "P-temp1" numbering) — unlike the legitimate cross-chunk reuse case
    above, where a later chunk deliberately omits a repeated entity from its
    own "entities" array and only references the earlier chunk's real id in
    a link. This must not silently conflate the two entities: the second
    chunk's colliding id should be renamed, and its own link remapped to
    point at the renamed (second) entity.
    """
    settings = Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=30)
    monkeypatch.setattr(document_ingest_module, "get_settings", lambda: settings)

    document_id = "D-collide1"
    doc_dir = tmp_path / document_id
    doc_dir.mkdir()
    (doc_dir / "notes.txt").write_text(
        "Ivan Petrov commands the unit.\n\nMaria Kuznetsova joined HQ."
    )
    document = {
        "document_id": document_id,
        "filename": "notes.txt",
        "file_type": "txt",
        "size_bytes": 100,
        "file_path": f"{document_id}/notes.txt",
        "status": "processing",
        "error_message": None,
        "batch_id": None,
        "uploaded_at": "2026-09-18T00:00:00+00:00",
    }
    session = FakeSession(document=document)
    monkeypatch.setattr(document_ingest_module, "get_driver", lambda: _FakeDriver(session))

    chunk_calls = []

    async def fake_complete_json(system_prompt, user_prompt):
        chunk_calls.append(user_prompt)
        if len(chunk_calls) == 1:
            return {
                "entities": [
                    {
                        "entity_id": "P-temp1",
                        "entity_class": "PERSON",
                        "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                        "label": "Ivan Petrov",
                        "confidence": "B2",
                        "source_ref": "notes.txt",
                    }
                ],
                "links": [],
            }
        # Second chunk independently (and coincidentally) numbers its own
        # brand-new entity "P-temp1" too — a DIFFERENT person, not a
        # reference back to chunk 1's Ivan Petrov.
        return {
            "entities": [
                {
                    "entity_id": "P-temp1",
                    "entity_class": "PERSON",
                    "entity_subclass": "PERSON.MILITARY_PERSONNEL",
                    "label": "Maria Kuznetsova",
                    "confidence": "B2",
                    "source_ref": "notes.txt",
                },
                {
                    "entity_id": "O-1",
                    "entity_class": "ORGANIZATION",
                    "entity_subclass": "ORGANIZATION.MILITARY_FORMATION.TACTICAL_FORMATION",
                    "label": "HQ",
                    "confidence": "B2",
                    "source_ref": "notes.txt",
                },
            ],
            "links": [
                {
                    "link_id": "L-1",
                    "link_type": "member_of",
                    "source_entity": "P-temp1",
                    "target_entity": "O-1",
                    "confidence": "B2",
                    "source_ref": "notes.txt",
                }
            ],
        }

    monkeypatch.setattr(extraction_agent_module, "complete_json", fake_complete_json)

    await document_ingest_module.process_document(document_id)

    assert len(chunk_calls) == 2
    assert session.created_batch is not None
    entities = json.loads(session.created_batch["entities"])
    links = json.loads(session.created_batch["links"])

    # Both entities survive as distinct rows — no overwrite/conflation.
    assert len(entities) == 3
    ids = [e["entity_id"] for e in entities]
    assert len(ids) == len(set(ids))  # every entity_id is unique

    ivan = next(e for e in entities if e["label"] == "Ivan Petrov")
    maria = next(e for e in entities if e["label"] == "Maria Kuznetsova")
    hq = next(e for e in entities if e["label"] == "HQ")
    assert ivan["entity_id"] == "P-temp1"  # first chunk's id is untouched
    assert maria["entity_id"] != "P-temp1"  # second chunk's collider was renamed
    assert maria["entity_id"] != ivan["entity_id"]

    # The second chunk's own link must resolve to the SECOND (renamed)
    # entity, not silently keep pointing at the first chunk's Ivan Petrov.
    assert len(links) == 1
    assert links[0]["source_entity"] == maria["entity_id"]
    assert links[0]["target_entity"] == hq["entity_id"]


@pytest.mark.asyncio
async def test_process_document_sets_error_status_on_parse_failure(tmp_path, monkeypatch):
    settings = Settings(upload_dir=str(tmp_path), max_upload_mb=20, ingest_chunk_chars=8000)
    monkeypatch.setattr(document_ingest_module, "get_settings", lambda: settings)

    document_id = "D-badfile1"
    doc_dir = tmp_path / document_id
    doc_dir.mkdir()
    (doc_dir / "broken.pdf").write_bytes(b"not a real pdf")
    document = {
        "document_id": document_id,
        "filename": "broken.pdf",
        "file_type": "pdf",
        "size_bytes": 14,
        "file_path": f"{document_id}/broken.pdf",
        "status": "processing",
        "error_message": None,
        "batch_id": None,
        "uploaded_at": "2026-09-18T00:00:00+00:00",
    }
    session = FakeSession(document=document)
    monkeypatch.setattr(document_ingest_module, "get_driver", lambda: _FakeDriver(session))

    await document_ingest_module.process_document(document_id)

    assert session.document["status"] == "error"
    assert session.document["error_message"]
