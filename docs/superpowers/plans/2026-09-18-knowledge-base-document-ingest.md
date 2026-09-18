# Knowledge Base: Document Upload & Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload PDF/DOCX/XLSX/TXT/MD files, extract text from them, run the existing LLM entity/link extraction pipeline over that text (chunked for long documents, merged into one review batch per document), and review/commit the result through a new "Knowledge base" page — reusing the existing ontology-validated `IngestBatch`/commit machinery end to end.

**Architecture:** New `Document` Neo4j node + `backend/app/services/document_parser.py` (bytes→text per format) + `backend/app/services/chunking.py` (paragraph-boundary chunking) + a small refactor of `backend/app/services/extraction_agent.py` to expose a reusable per-chunk extract-and-validate core + `backend/app/services/document_ingest.py` (upload→disk, background chunk-and-merge orchestration) + two new API surfaces (`/documents/*`, `GET /ingest/{batch_id}`) + a new frontend page reusing a shared review-table component extracted out of the existing `IngestDialog`.

**Tech Stack:** FastAPI + Pydantic v2 + async `neo4j` driver (backend), React + Vite + Tailwind + Radix UI (frontend); new deps `pypdf`, `python-docx`, `python-multipart`.

**Spec:** `docs/superpowers/specs/2026-09-18-knowledge-base-document-ingest-design.md`

## Global Constraints

- Supported upload extensions: `.txt .md .pdf .docx .xlsx` only — no legacy `.doc`/`.xls`, no OCR.
- Uploaded files are stored on disk under `settings.upload_dir` (default `"uploads"`), one subdirectory per `document_id`.
- `extract_from_text` (used by today's `POST /ingest` paste-text flow) must keep its exact current external behavior — the existing 3 tests in `test_extraction_agent.py` must still pass unchanged after the refactor.
- A document's chunks are merged into exactly one `IngestBatch` (never one batch per chunk).
- Extraction runs via FastAPI `BackgroundTasks` — no new queue/worker infrastructure.
- All new Python code follows this repo's existing conventions: async `neo4j.AsyncSession`, `HTTPException` for API errors, `ValueError` for validation errors surfaced as `400`, single-line Cypher for simple by-id lookups (matching `entities.py`/`ingest.py`), pytest with `FakeSession`-style mocking (no real DB/LLM in unit tests, matching `test_extraction_agent.py`).
- Frontend has no automated test suite for dialogs/pages (only `InteractiveMap.test.tsx`'s Cesium mock exists) — new UI is verified manually via the running dev app, matching current project practice.

---

## Task 1: Backend config — upload settings

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.upload_dir: str` (default `"uploads"`), `Settings.max_upload_mb: int` (default `20`), `Settings.ingest_chunk_chars: int` (default `8000`) — consumed by Tasks 5 and 6.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config.py`:

```python
def test_upload_settings_have_sane_defaults():
    settings = Settings(upload_dir="uploads", max_upload_mb=20, ingest_chunk_chars=8000)
    assert settings.upload_dir == "uploads"
    assert settings.max_upload_mb == 20
    assert settings.ingest_chunk_chars == 8000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/pytest tests/test_config.py::test_upload_settings_have_sane_defaults -v`
Expected: FAIL — `TypeError: Settings.__init__() got unexpected keyword arguments` (fields don't exist yet).

- [ ] **Step 3: Add the settings fields**

In `backend/app/config.py`, add after the `feeds_enabled: bool = True` line:

```python
    # Knowledge-base document uploads (PDF/DOCX/XLSX/TXT/MD -> extraction).
    # upload_dir is relative to the backend process's working directory by
    # default; in production (Railway) it must point at a mounted
    # persistent volume, or uploaded files are lost on the next deploy.
    upload_dir: str = "uploads"
    max_upload_mb: int = 20
    ingest_chunk_chars: int = 8000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/pytest tests/test_config.py -v`
Expected: PASS (all tests in the file, including the new one and the 3 pre-existing CORS ones).

- [ ] **Step 5: Document the new env vars**

In `.env.example`, add after the `# Backend` section (after the `CORS_ALLOWED_ORIGINS=*` line):

```
# Knowledge base document uploads (PDF/DOCX/XLSX/TXT/MD).
# UPLOAD_DIR is relative to the backend process's working directory by
# default. In production (Railway), mount a persistent volume at this path
# — Railway's filesystem is otherwise ephemeral and uploaded files will be
# lost on the next deploy.
UPLOAD_DIR=uploads
MAX_UPLOAD_MB=20
INGEST_CHUNK_CHARS=8000
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py .env.example
git commit -m "feat(backend): add upload_dir/max_upload_mb/ingest_chunk_chars settings"
```

---

## Task 2: Document text extraction (`document_parser.py`)

**Files:**
- Create: `backend/app/services/document_parser.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_document_parser.py`

**Interfaces:**
- Produces: `extract_text(filename: str, content: bytes) -> str`, raising `ValueError` on an unsupported extension, unparseable content, or no extractable text — consumed by Task 5 (`document_ingest.py`).

- [ ] **Step 1: Add the new dependencies**

In `backend/requirements.txt`, add two lines (anywhere near `openpyxl>=3.1`):

```
pypdf>=5.0
python-docx>=1.1
```

Run: `cd backend && venv/bin/pip install pypdf python-docx`

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_document_parser.py`:

```python
"""Unit tests for extract_text: bytes -> text per file format. PDF
fixtures are hand-built minimal PDFs (no PDF-writing library needed);
docx/xlsx fixtures are built with the same libraries extract_text reads
them with — a realistic round-trip, since real uploads are themselves
OOXML files produced by Word/Excel/python-docx/openpyxl.
"""
import io

import openpyxl
import pytest
from docx import Document as DocxDocument

from app.services.document_parser import extract_text


def _build_minimal_pdf(text: str) -> bytes:
    """Hand-built single-page PDF with one extractable text stream —
    avoids a PDF-writing dependency just for test fixtures."""
    content_stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 200 200] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content_stream)).encode() + b" >>\nstream\n"
        + content_stream + b"\nendstream",
    ]
    header = b"%PDF-1.4\n"
    body = bytearray()
    offsets = [0]
    pos = len(header)
    for i, obj in enumerate(objects, start=1):
        offsets.append(pos)
        piece = f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
        body += piece
        pos += len(piece)
    xref_offset = pos
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode()
    return header + bytes(body) + xref + trailer


def _build_docx(paragraphs: list[str]) -> bytes:
    doc = DocxDocument()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_xlsx(rows: list[list[str]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Contacts"
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def test_extract_text_txt():
    assert extract_text("notes.txt", b"hello world") == "hello world"


def test_extract_text_md():
    assert extract_text("notes.md", b"# Heading\n\nBody text") == "# Heading\n\nBody text"


def test_extract_text_pdf():
    content = _build_minimal_pdf("Hello World")
    assert "Hello World" in extract_text("report.pdf", content)


def test_extract_text_docx():
    content = _build_docx(["First paragraph.", "Second paragraph."])
    text = extract_text("report.docx", content)
    assert "First paragraph." in text
    assert "Second paragraph." in text


def test_extract_text_xlsx():
    content = _build_xlsx([["Name", "Role"], ["Ivan Petrov", "Commander"]])
    text = extract_text("roster.xlsx", content)
    assert "# Sheet: Contacts" in text
    assert "Ivan Petrov" in text
    assert "Commander" in text


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="unsupported"):
        extract_text("notes.csv", b"a,b,c")


def test_extract_text_rejects_corrupt_pdf():
    with pytest.raises(ValueError, match="failed to parse"):
        extract_text("broken.pdf", b"not a real pdf")


def test_extract_text_rejects_corrupt_docx():
    with pytest.raises(ValueError, match="failed to parse"):
        extract_text("broken.docx", b"not a real docx")


def test_extract_text_rejects_empty_txt():
    with pytest.raises(ValueError, match="no extractable text"):
        extract_text("empty.txt", b"   ")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_document_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.document_parser'`

- [ ] **Step 4: Implement `document_parser.py`**

Create `backend/app/services/document_parser.py`:

```python
"""Extract plain text from an uploaded knowledge-base file, dispatching on
its extension. This module's only job is "bytes -> text"; the extracted
text then feeds the same LLM extraction pipeline used by pasted-text
ingest (app.services.extraction_agent).
"""
import io

import openpyxl
from docx import Document as DocxDocument
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {"txt", "md", "pdf", "docx", "xlsx"}


def extract_text(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported file type: '.{ext}'")

    try:
        if ext in ("txt", "md"):
            text = content.decode("utf-8")
        elif ext == "pdf":
            text = _extract_pdf(content)
        elif ext == "docx":
            text = _extract_docx(content)
        else:  # xlsx
            text = _extract_xlsx(content)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"failed to parse '.{ext}' file: {e}") from e

    if not text.strip():
        raise ValueError(f"no extractable text found in '.{ext}' file")
    return text


def _extract_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p for p in pages if p.strip())


def _extract_docx(content: bytes) -> str:
    doc = DocxDocument(io.BytesIO(content))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n\n".join(parts)


def _extract_xlsx(content: bytes) -> str:
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    parts = []
    for sheet in workbook.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_document_parser.py -v`
Expected: PASS (all 9 tests). If the PDF test fails to parse, inspect the `pypdf` error message — the hand-built PDF's xref offsets are computed from the exact byte lengths written, so a mismatch usually means a stray encoding difference; use `superpowers:systematic-debugging` if it doesn't resolve quickly.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/document_parser.py backend/tests/test_document_parser.py backend/requirements.txt
git commit -m "feat(backend): add document_parser.extract_text for pdf/docx/xlsx/txt/md"
```

---

## Task 3: Text chunking (`chunking.py`)

**Files:**
- Create: `backend/app/services/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Interfaces:**
- Produces: `chunk_text(text: str, max_chars: int = 8000) -> list[str]` — consumed by Task 5 (`document_ingest.py`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_chunking.py`:

```python
"""Unit tests for chunk_text: paragraph-boundary packing so a long
document can be extracted in LLM-sized pieces without splitting
mid-sentence when avoidable."""
from app.services.chunking import chunk_text


def test_chunk_text_returns_single_chunk_when_under_limit():
    text = "Paragraph one.\n\nParagraph two."
    assert chunk_text(text, max_chars=8000) == [text]


def test_chunk_text_splits_on_paragraph_boundaries_when_over_limit():
    para1 = "Ivan Petrov commands the unit."  # 30 chars
    para2 = "Ivan Petrov reports to HQ."      # 26 chars
    text = f"{para1}\n\n{para2}"

    chunks = chunk_text(text, max_chars=30)

    assert chunks == [para1, para2]


def test_chunk_text_hard_splits_a_single_oversized_paragraph():
    paragraph = "x" * 250
    chunks = chunk_text(paragraph, max_chars=100)

    assert len(chunks) == 3
    assert "".join(chunks) == paragraph
    assert all(len(c) <= 100 for c in chunks)


def test_chunk_text_returns_empty_list_for_blank_text():
    assert chunk_text("   \n\n  ", max_chars=8000) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_chunking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.chunking'`

- [ ] **Step 3: Implement `chunking.py`**

Create `backend/app/services/chunking.py`:

```python
"""Split extracted document text into LLM-sized chunks on paragraph
boundaries, so a document larger than one extraction call can still be
processed chunk-by-chunk into a single merged review batch
(see app.services.document_ingest.process_document).
"""


def chunk_text(text: str, max_chars: int = 8000) -> list[str]:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    # A paragraph longer than max_chars is hard-split rather than dropped.
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            for start in range(0, len(para), max_chars):
                pieces.append(para[start : start + max_chars])

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in pieces:
        extra = len(piece) + (2 if current else 0)  # +2 for the "\n\n" join
        if current and current_len + extra > max_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
            extra = len(piece)
        current.append(piece)
        current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_chunking.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chunking.py backend/tests/test_chunking.py
git commit -m "feat(backend): add chunking.chunk_text for long-document extraction"
```

---

## Task 4: Refactor `extraction_agent.py` for per-chunk reuse

**Files:**
- Modify: `backend/app/services/extraction_agent.py`
- Modify: `backend/tests/test_extraction_agent.py`

**Interfaces:**
- Consumes: nothing new (same `AsyncSession`, `complete_json` as today).
- Produces: `_extract_and_validate(session, text, extra_existing_entities=None) -> dict` with keys `valid_entities`, `rejected_entities`, `valid_links`, `rejected_links` — consumed by Task 5 (`document_ingest.process_document`). `extract_from_text(session, text) -> dict` keeps its exact current signature/behavior, now persisting `rejected_entities`/`rejected_links` on the `IngestBatch` node in addition to `entities`/`links` (previously only returned inline, never persisted — needed so the new `GET /ingest/{batch_id}` in Task 7 can reconstruct the full batch from the graph).

- [ ] **Step 1: Confirm the pre-refactor baseline passes**

Run: `cd backend && venv/bin/pytest tests/test_extraction_agent.py -v`
Expected: PASS (all 3 existing tests) — this is the regression baseline the refactor must not break.

- [ ] **Step 2: Write the new failing test for cross-chunk context**

Add to `backend/tests/test_extraction_agent.py` (uses the existing `FakeSession`/`FakeRecord`/`FakeListResult`/`FakeSingleResult` and `VALID_*` fixtures already in the file):

```python
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
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && venv/bin/pytest tests/test_extraction_agent.py::test_extract_and_validate_folds_extra_existing_entities_into_prompt_and_validation -v`
Expected: FAIL — `AttributeError: module 'app.services.extraction_agent' has no attribute '_extract_and_validate'`

- [ ] **Step 4: Refactor `extraction_agent.py`**

Replace the body of `extract_from_text` (everything from `async def extract_from_text` to the end of the file, i.e. lines 134–213 of the current file) with:

```python
async def _extract_and_validate(
    session: AsyncSession, text: str, extra_existing_entities: list[dict] | None = None
) -> dict:
    """Call the LLM, validate each proposed entity/link, return the valid +
    rejected rows — no IngestBatch is persisted here, so this can be called
    once per chunk of a larger document without creating a batch per chunk
    (see app.services.document_ingest.process_document). extra_existing_entities
    lets a caller fold entities accepted from earlier chunks of the same
    document into this chunk's "existing entities" context, so a repeated
    mention across chunks reuses the same entity_id instead of duplicating it.
    """
    class_keys, link_defs, inverse_to_forward = await _fetch_ontology_vocab(session)
    existing_entities = await _fetch_existing_entities(session)
    if extra_existing_entities:
        existing_entities = existing_entities + extra_existing_entities

    system_prompt = PROMPT_TEMPLATE.format(
        entity_subclasses="\n".join(class_keys),
        link_types=_format_link_types(link_defs),
        existing_entities=_format_existing_entities(existing_entities),
    )
    raw = await complete_json(system_prompt, text)

    # Belt-and-suspenders: even if the model emits a documented inverse name
    # despite the instructions (e.g. "operated_by" instead of "operator_of"),
    # normalize it to the canonical forward type and swap source/target
    # rather than rejecting a link the ontology actually supports.
    for row in raw.get("links", []):
        forward = inverse_to_forward.get(row.get("link_type"))
        if forward:
            row["link_type"] = forward
            row["source_entity"], row["target_entity"] = (
                row.get("target_entity"),
                row.get("source_entity"),
            )

    valid_entities, rejected_entities = [], []
    for row in raw.get("entities", []):
        try:
            entity = EntityCreate(**row)
            await validate_entity(session, entity)
            valid_entities.append(entity.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as e:
            rejected_entities.append({"row": row, "reason": str(e)})

    # A link's endpoints may be a brand-new entity from this batch, or a real
    # entity_id the model chose to reuse from EXISTING_ENTITIES — both are
    # valid targets; anything else is a hallucinated reference.
    class_by_id = {e["entity_id"]: e["entity_class"] for e in valid_entities}
    class_by_id.update({e["entity_id"]: e["entity_class"] for e in existing_entities})

    valid_links, rejected_links = [], []
    for row in raw.get("links", []):
        try:
            link = LinkCreate(**row)
            if link.source_entity not in class_by_id or link.target_entity not in class_by_id:
                raise ValidationError(
                    "link references an entity not in this batch's valid set "
                    "and not an existing entity_id"
                )
            await validate_link(
                session, link, class_by_id[link.source_entity], class_by_id[link.target_entity]
            )
            valid_links.append(link.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as e:
            rejected_links.append({"row": row, "reason": str(e)})

    return {
        "valid_entities": valid_entities,
        "rejected_entities": rejected_entities,
        "valid_links": valid_links,
        "rejected_links": rejected_links,
    }


async def extract_from_text(session: AsyncSession, text: str) -> dict:
    """Single-shot extraction for pasted scenario text: validate via
    _extract_and_validate, then persist as a standalone proposed
    IngestBatch (unlike document_ingest.process_document, which merges
    several chunks' results into one batch before persisting).
    """
    result = await _extract_and_validate(session, text)
    batch_id = f"B-{uuid.uuid4().hex[:8]}"
    batch = {
        "batch_id": batch_id,
        "status": "proposed",
        "source_text": text,
        "entities": result["valid_entities"],
        "links": result["valid_links"],
        "rejected_entities": result["rejected_entities"],
        "rejected_links": result["rejected_links"],
    }
    await session.run(
        """
        CREATE (b:IngestBatch {batch_id: $batch_id, status: $status, source_text: $source_text,
                                entities: $entities, links: $links,
                                rejected_entities: $rejected_entities, rejected_links: $rejected_links})
        """,
        batch_id=batch_id,
        status="proposed",
        source_text=text,
        entities=json.dumps(result["valid_entities"]),
        links=json.dumps(result["valid_links"]),
        rejected_entities=json.dumps(result["rejected_entities"]),
        rejected_links=json.dumps(result["rejected_links"]),
    )
    return batch
```

- [ ] **Step 5: Run the full test file to verify everything passes**

Run: `cd backend && venv/bin/pytest tests/test_extraction_agent.py -v`
Expected: PASS (all 4 tests: the 3 pre-existing regression tests unchanged, plus the new one).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/extraction_agent.py backend/tests/test_extraction_agent.py
git commit -m "refactor(backend): split extract_from_text into reusable _extract_and_validate"
```

---

## Task 5: Document model + upload/extraction orchestrator

**Files:**
- Create: `backend/app/models/document.py`
- Create: `backend/app/services/document_ingest.py`
- Test: `backend/tests/test_document_ingest.py`

**Interfaces:**
- Consumes: `Settings.upload_dir/max_upload_mb/ingest_chunk_chars` (Task 1), `extract_text` (Task 2), `chunk_text` (Task 3), `_extract_and_validate` (Task 4).
- Produces: `Document` Pydantic model (fields: `document_id, filename, file_type, size_bytes, file_path, status, error_message, batch_id, uploaded_at`); `save_upload(session, filename, content) -> dict`, `process_document(document_id) -> None` — consumed by Task 6 (`documents.py` API router).

- [ ] **Step 1: Create the `Document` model**

Create `backend/app/models/document.py`:

```python
"""Document Pydantic model — an uploaded knowledge-base file and its
extraction status, mirroring the Document Neo4j node."""
from typing import Literal

from pydantic import BaseModel

DocumentStatus = Literal["processing", "proposed", "committed", "error"]
DocumentFileType = Literal["txt", "md", "pdf", "docx", "xlsx"]


class Document(BaseModel):
    document_id: str
    filename: str
    file_type: DocumentFileType
    size_bytes: int
    file_path: str
    status: DocumentStatus
    error_message: str | None = None
    batch_id: str | None = None
    uploaded_at: str
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_document_ingest.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_document_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.document_ingest'`

- [ ] **Step 4: Implement `document_ingest.py`**

Create `backend/app/services/document_ingest.py`:

```python
"""Document upload orchestration: persist an uploaded file, then run
chunked extraction against it in the background, merging all chunks into
one review batch — the file-upload counterpart to extract_from_text.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from neo4j import AsyncSession

from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.services.chunking import chunk_text
from app.services.document_parser import extract_text
from app.services.extraction_agent import _extract_and_validate

ALLOWED_EXTENSIONS = {"txt", "md", "pdf", "docx", "xlsx"}


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def save_upload(session: AsyncSession, filename: str, content: bytes) -> dict:
    """Validate, write to disk, and record a new Document node with
    status='processing'. Raises ValueError on a rejected extension or an
    over-limit file — the caller (POST /documents/upload) turns that into
    a 400 before any Document node is created.
    """
    safe_filename = Path(filename).name or "upload"  # strip any path components
    ext = _extension(safe_filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"unsupported file type '.{ext}' — allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"file exceeds {settings.max_upload_mb}MB upload limit")

    document_id = f"D-{uuid.uuid4().hex[:8]}"
    doc_dir = Path(settings.upload_dir) / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / safe_filename).write_bytes(content)

    doc = {
        "document_id": document_id,
        "filename": safe_filename,
        "file_type": ext,
        "size_bytes": len(content),
        "file_path": f"{document_id}/{safe_filename}",
        "status": "processing",
        "error_message": None,
        "batch_id": None,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    await session.run("CREATE (d:Document $props)", props=doc)
    return doc


async def process_document(document_id: str) -> None:
    """Background job: parse, chunk, and extract a previously-uploaded
    document, merging every chunk's results into a single review batch.
    Runs detached from the upload request (scheduled via BackgroundTasks),
    so failures are recorded on the Document node rather than raised.
    """
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            return
        doc = dict(record["d"])

        try:
            settings = get_settings()
            file_bytes = (Path(settings.upload_dir) / doc["file_path"]).read_bytes()
            text = extract_text(doc["filename"], file_bytes)
            chunks = chunk_text(text, max_chars=settings.ingest_chunk_chars)

            pending_entities: list[dict] = []
            valid_entities: list[dict] = []
            rejected_entities: list[dict] = []
            valid_links: list[dict] = []
            rejected_links: list[dict] = []
            for chunk in chunks:
                chunk_result = await _extract_and_validate(
                    session, chunk, extra_existing_entities=pending_entities
                )
                valid_entities.extend(chunk_result["valid_entities"])
                rejected_entities.extend(chunk_result["rejected_entities"])
                valid_links.extend(chunk_result["valid_links"])
                rejected_links.extend(chunk_result["rejected_links"])
                pending_entities.extend(chunk_result["valid_entities"])

            batch_id = f"B-{uuid.uuid4().hex[:8]}"
            await session.run(
                """
                CREATE (b:IngestBatch {batch_id: $batch_id, status: $status, source_text: $source_text,
                                        entities: $entities, links: $links,
                                        rejected_entities: $rejected_entities, rejected_links: $rejected_links,
                                        document_id: $document_id})
                """,
                batch_id=batch_id,
                status="proposed",
                source_text=text,
                entities=json.dumps(valid_entities),
                links=json.dumps(valid_links),
                rejected_entities=json.dumps(rejected_entities),
                rejected_links=json.dumps(rejected_links),
                document_id=document_id,
            )
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = $status, d.batch_id = $batch_id",
                id=document_id,
                status="proposed",
                batch_id=batch_id,
            )
        except Exception as e:
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = $status, d.error_message = $error_message",
                id=document_id,
                status="error",
                error_message=str(e),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_document_ingest.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/document.py backend/app/services/document_ingest.py backend/tests/test_document_ingest.py
git commit -m "feat(backend): add Document model and save_upload/process_document orchestration"
```

---

## Task 6: `/documents` API router

**Files:**
- Create: `backend/app/api/documents.py`
- Modify: `backend/app/main.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_documents_api.py`

**Interfaces:**
- Consumes: `save_upload`, `process_document` (Task 5), `Document` model (Task 5).
- Produces: `POST /documents/upload`, `GET /documents`, `GET /documents/{document_id}`, `GET /documents/{document_id}/file` — consumed by Task 8 (frontend `api.ts`).

- [ ] **Step 1: Add the multipart-parsing dependency**

FastAPI's `UploadFile` requires `python-multipart` at runtime (not currently a dependency). In `backend/requirements.txt`, add:

```
python-multipart>=0.0.9
```

Run: `cd backend && venv/bin/pip install python-multipart`

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_documents_api.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_documents_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.documents'`

- [ ] **Step 4: Implement `documents.py`**

Create `backend/app/api/documents.py`:

```python
"""Document upload API: multipart upload -> disk storage (via
document_ingest.save_upload) -> background extraction (process_document),
producing one proposed IngestBatch per document for review through the
existing /ingest endpoints.
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.models.document import Document
from app.services.document_ingest import process_document, save_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=Document)
async def upload_document(file: UploadFile, background_tasks: BackgroundTasks):
    content = await file.read()
    driver = get_driver()
    async with driver.session() as session:
        try:
            doc = await save_upload(session, file.filename or "upload", content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    background_tasks.add_task(process_document, doc["document_id"])
    return doc


@router.get("", response_model=list[Document])
async def list_documents():
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (d:Document) RETURN d ORDER BY d.uploaded_at DESC")
        return [dict(record["d"]) async for record in result]


@router.get("/{document_id}", response_model=Document)
async def get_document(document_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        return dict(record["d"])


@router.get("/{document_id}/file")
async def download_document(document_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        doc = dict(record["d"])

    full_path = Path(get_settings().upload_dir) / doc["file_path"]
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="file not found on disk")
    return FileResponse(full_path, filename=doc["filename"])
```

- [ ] **Step 5: Wire the router into `main.py`**

In `backend/app/main.py`, change:

```python
    from app.api import entities, feeds, ingest, links, schema, scenarios

    app.include_router(entities.router)
    app.include_router(links.router)
    app.include_router(schema.router)
    app.include_router(scenarios.router)
    app.include_router(ingest.router)
    app.include_router(feeds.router)
```

to:

```python
    from app.api import documents, entities, feeds, ingest, links, schema, scenarios

    app.include_router(entities.router)
    app.include_router(links.router)
    app.include_router(schema.router)
    app.include_router(scenarios.router)
    app.include_router(ingest.router)
    app.include_router(documents.router)
    app.include_router(feeds.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_documents_api.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 7: Run the full backend test suite to check for regressions**

Run: `cd backend && venv/bin/pytest -v`
Expected: PASS (every test in `backend/tests/`, including the pre-existing feed/entity/ontology suites untouched by this plan).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/documents.py backend/app/main.py backend/tests/test_documents_api.py backend/requirements.txt
git commit -m "feat(backend): add /documents upload/list/get/file API"
```

---

## Task 7: `GET /ingest/{batch_id}` + commit-side Document flip

**Files:**
- Modify: `backend/app/api/ingest.py`
- Test: `backend/tests/test_ingest_api.py`

**Interfaces:**
- Produces: `GET /ingest/{batch_id}` returning `{batch_id, status, source_text, entities, links, rejected_entities, rejected_links}` — consumed by Task 8 (frontend `api.getBatch`). `POST /ingest/{batch_id}/commit` gains a side effect: if the batch has a `document_id`, the matching `Document.status` is set to `committed`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ingest_api.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: FAIL — `404` instead of `200` for `GET /ingest/B-1` (route doesn't exist yet → FastAPI 404), and the commit test's document-flip assertion fails (`document["status"] == "proposed"`, not `"committed"`).

- [ ] **Step 3: Implement the changes in `ingest.py`**

In `backend/app/api/ingest.py`, add a new route between `ingest_text` and `commit_batch`:

```python
@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) RETURN b", id=batch_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="batch not found")
        batch = dict(record["b"])
        return {
            "batch_id": batch["batch_id"],
            "status": batch["status"],
            "source_text": batch.get("source_text", ""),
            "entities": json.loads(batch["entities"]),
            "links": json.loads(batch["links"]),
            "rejected_entities": json.loads(batch.get("rejected_entities") or "[]"),
            "rejected_links": json.loads(batch.get("rejected_links") or "[]"),
        }
```

Then, in `commit_batch`, change the tail from:

```python
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.status = 'committed'", id=batch_id
        )
        return {"batch_id": batch_id, "status": "committed", "entities": len(entities), "links": len(links)}
```

to:

```python
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.status = 'committed'", id=batch_id
        )
        if batch.get("document_id"):
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = 'committed'",
                id=batch["document_id"],
            )
        return {"batch_id": batch_id, "status": "committed", "entities": len(entities), "links": len(links)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && venv/bin/pytest -v`
Expected: PASS (every test in `backend/tests/`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/ingest.py backend/tests/test_ingest_api.py
git commit -m "feat(backend): add GET /ingest/{batch_id} and commit-side Document flip"
```

---

## Task 8: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `Document` type; `api.uploadDocument(file)`, `api.listDocuments()`, `api.getDocument(id)`, `api.getBatch(id)` — consumed by Tasks 10 and 11.

- [ ] **Step 1: Add the `Document` type**

In `frontend/src/lib/types.ts`, add at the end of the file:

```ts
export type DocumentStatus = 'processing' | 'proposed' | 'committed' | 'error'
export type DocumentFileType = 'txt' | 'md' | 'pdf' | 'docx' | 'xlsx'

export interface Document {
  document_id: string
  filename: string
  file_type: DocumentFileType
  size_bytes: number
  file_path: string
  status: DocumentStatus
  error_message: string | null
  batch_id: string | null
  uploaded_at: string
}
```

- [ ] **Step 2: Add `requestForm` and the new `api` methods**

In `frontend/src/lib/api.ts`, add `Document` to the type import list at the top:

```ts
import type {
  ClassDef,
  CommitResult,
  Document,
  Entity,
  EntityCreateInput,
  ExplainResponse,
  FeedPollResult,
  FeedStatus,
  IngestBatch,
  Link,
  LinkCreateInput,
  LinkDef,
  ScopeResponse,
} from './types'
```

Add `requestForm` right after `request`:

```ts
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  // No content-type header here — the browser sets the multipart boundary
  // itself; request()'s forced 'application/json' would corrupt the body.
  const res = await fetch(`${BASE_URL}${path}`, { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }
  return res.json()
}
```

Add to the `api` object, after `commitBatch`:

```ts
  getBatch: (batchId: string) => request<IngestBatch>(`/ingest/${encodeURIComponent(batchId)}`),
  uploadDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return requestForm<Document>('/documents/upload', form)
  },
  listDocuments: () => request<Document[]>('/documents'),
  getDocument: (id: string) => request<Document>(`/documents/${encodeURIComponent(id)}`),
```

- [ ] **Step 3: Verify the frontend still typechecks**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors (this file has no other consumers yet, so nothing else changes).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): add Document type and documents/getBatch API client methods"
```

---

## Task 9: Extract `BatchReviewPanel` out of `IngestDialog`

**Files:**
- Create: `frontend/src/components/layout/BatchReviewPanel.tsx`
- Modify: `frontend/src/components/layout/IngestDialog.tsx`

**Interfaces:**
- Produces: `<BatchReviewPanel batch commit={onCommit} committing commitError extraActions? />` — consumed by Task 11 (`DocumentReviewDialog`).

- [ ] **Step 1: Create `BatchReviewPanel.tsx`**

Create `frontend/src/components/layout/BatchReviewPanel.tsx` — this is the entities/links/rejected review table + commit button, extracted verbatim out of `IngestDialog.tsx`'s review block, with an optional `extraActions` slot so a caller can add a dialog-specific button (e.g. IngestDialog's "Start over") on the same row as Commit without leaking that concept into the shared component:

```tsx
import type { ReactNode } from 'react'
import type { IngestBatch } from '../../lib/types'
import { Button } from '../ui/Button'
import { ConfidenceChip, ProposedChip } from '../ui/Chip'

interface BatchReviewPanelProps {
  batch: IngestBatch
  onCommit: () => void
  committing: boolean
  commitError: string
  /** Extra button(s) rendered before Commit, e.g. IngestDialog's "Start
   * over" — DocumentReviewDialog has no equivalent and omits this. */
  extraActions?: ReactNode
}

/** Entities/links/rejected review table + commit button — shared between
 * IngestDialog (paste-text flow, batch held in local state right after
 * extraction) and DocumentReviewDialog (upload flow, batch fetched by id
 * from a background-produced IngestBatch). This component only renders
 * and reports commit intent via onCommit; it never fetches or resets. */
export function BatchReviewPanel({
  batch,
  onCommit,
  committing,
  commitError,
  extraActions,
}: BatchReviewPanelProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Proposed from this text — review before committing to the graph.
      </p>

      <ResultTable title={`Entities (${batch.entities.length})`} empty="No entities extracted.">
        {batch.entities.map((e) => (
          <tr key={e.entity_id} className="border-b border-[var(--color-border)]">
            <td className="py-1.5 pe-3">{e.label}</td>
            <td className="py-1.5 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
              {e.entity_subclass}
            </td>
            <td className="py-1.5 pe-3">
              <ConfidenceChip code={e.confidence} />
            </td>
            <td className="py-1.5 pe-3">
              <ProposedChip />
            </td>
          </tr>
        ))}
      </ResultTable>

      <ResultTable title={`Links (${batch.links.length})`} empty="No links extracted.">
        {batch.links.map((l) => (
          <tr key={l.link_id} className="border-b border-[var(--color-border)]">
            <td className="py-1.5 pe-3 font-mono text-xs">{l.link_type}</td>
            <td className="py-1.5 pe-3 font-mono text-xs">{l.source_entity}</td>
            <td className="py-1.5 pe-3 font-mono text-xs">{l.target_entity}</td>
            <td className="py-1.5 pe-3">
              <ConfidenceChip code={l.confidence} />
            </td>
          </tr>
        ))}
      </ResultTable>

      {(batch.rejected_entities.length > 0 || batch.rejected_links.length > 0) && (
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            Rejected ({batch.rejected_entities.length + batch.rejected_links.length})
          </h3>
          <ul className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
            {[...batch.rejected_entities, ...batch.rejected_links].map((r, i) => (
              <li key={i}>{r.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {commitError && (
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
          {commitError}
        </p>
      )}

      <div className="flex justify-end gap-2">
        {extraActions}
        <Button
          variant="primary"
          onClick={onCommit}
          disabled={(batch.entities.length === 0 && batch.links.length === 0) || committing}
        >
          {committing ? 'Committing…' : 'Commit to graph'}
        </Button>
      </div>
    </div>
  )
}

function ResultTable({
  title,
  empty,
  children,
}: {
  title: string
  empty: string
  children: ReactNode
}) {
  const hasRows = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {title}
      </h3>
      {hasRows ? (
        <table className="w-full border-collapse text-sm">
          <tbody>{children}</tbody>
        </table>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">{empty}</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Update `IngestDialog.tsx` to use it**

In `frontend/src/components/layout/IngestDialog.tsx`, replace the entire block from `{(phase === 'review' || phase === 'committing') && batch && (` through its matching closing `)}` (the block containing the two `ResultTable` calls, the rejected section, `commitError`, and the button row) with:

```tsx
          {(phase === 'review' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
              extraActions={
                <Button onClick={reset} disabled={phase === 'committing'}>
                  Start over
                </Button>
              }
            />
          )}
```

Delete the now-unused local `ResultTable` function at the bottom of `IngestDialog.tsx` (it moved into `BatchReviewPanel.tsx`) — remove the whole `function ResultTable({ title, empty, children }: {...}) { ... }` block.

That markup was the only user of three imports in this file, which are now unused and must be removed too (a `noUnusedLocals` build will fail otherwise). Change the top of `IngestDialog.tsx` from:

```tsx
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { api, ApiError } from '../../lib/api'
import { SAMPLE_SCENARIOS } from '../../lib/sampleScenarios'
import type { IngestBatch } from '../../lib/types'
import { Button } from '../ui/Button'
import { ConfidenceChip, ProposedChip } from '../ui/Chip'
```

to:

```tsx
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { SAMPLE_SCENARIOS } from '../../lib/sampleScenarios'
import type { IngestBatch } from '../../lib/types'
import { Button } from '../ui/Button'
import { BatchReviewPanel } from './BatchReviewPanel'
```

(`ReactNode` was only used by the now-deleted `ResultTable`'s `children` prop; `ConfidenceChip`/`ProposedChip` were only used inside the now-deleted review table markup — both extraction tables now live in `BatchReviewPanel.tsx`, which imports them itself.)

- [ ] **Step 3: Manually verify no behavior change**

Run: `cd frontend && npm run dev`, open the app, click **Ingest scenario…**, paste one of the sample scenarios, click **Extract entities & links**, confirm the review table (entities/links/rejected) renders identically to before, and that both **Start over** and **Commit to graph** still work (Start over returns to the draft textarea; Commit shows the "Committed" confirmation screen).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/BatchReviewPanel.tsx frontend/src/components/layout/IngestDialog.tsx
git commit -m "refactor(frontend): extract BatchReviewPanel out of IngestDialog"
```

---

## Task 10: `KnowledgeBasePage` + navigation wiring

**Files:**
- Create: `frontend/src/components/knowledge/KnowledgeBasePage.tsx`
- Modify: `frontend/src/components/layout/LeftRail.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api.listDocuments`, `api.uploadDocument` (Task 8).
- Produces: `<KnowledgeBasePage />` (self-contained; owns its own review-dialog state) — the `DocumentReviewDialog` it renders is implemented in Task 11, so this task temporarily renders a placeholder `<div>` in its place, replaced in Task 11's Step 1.

- [ ] **Step 1: Create `KnowledgeBasePage.tsx`**

Create `frontend/src/components/knowledge/KnowledgeBasePage.tsx`:

```tsx
import { Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { Document } from '../../lib/types'

const REFRESH_MS = 3000

const STATUS_COLOR: Record<Document['status'], string> = {
  processing: 'var(--color-status-unknown)',
  proposed: 'var(--color-status-active)',
  committed: 'var(--color-status-inactive)',
  error: 'var(--color-status-destroyed)',
}

function DocumentStatusChip({ status }: { status: Document['status'] }) {
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-inverse)]"
      style={{ background: STATUS_COLOR[status] }}
    >
      {status}
    </span>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Upload documents (pdf/docx/xlsx/txt/md) and browse the resulting
 * extraction status — the file-upload counterpart to IngestDialog's
 * paste-text flow. Extraction runs in the background server-side; this
 * page polls GET /documents so status updates without any push mechanism. */
export function KnowledgeBasePage() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [loadError, setLoadError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [reviewDocId, setReviewDocId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const result = await api.listDocuments()
        if (!cancelled) {
          setDocuments(result)
          setLoadError('')
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        }
      }
    }
    refresh()
    const id = setInterval(refresh, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (!file) return
    setUploading(true)
    setUploadError('')
    try {
      const doc = await api.uploadDocument(file)
      setDocuments((prev) => [doc, ...prev])
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setUploading(false)
    }
  }

  const reviewDoc = documents.find((d) => d.document_id === reviewDocId) ?? null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--color-border)] px-6 py-4">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Knowledge base</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Upload a PDF, Word, text, Markdown, or Excel file — the extraction agent proposes
          entities and links from its content for you to review and commit.
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            id="document-upload"
            accept=".txt,.md,.pdf,.docx,.xlsx"
            onChange={handleFileChange}
            disabled={uploading}
            className="hidden"
          />
          <label
            htmlFor="document-upload"
            className={`inline-flex cursor-pointer items-center gap-2 rounded-md bg-[var(--color-focus)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-inverse)] hover:brightness-110 ${uploading ? 'pointer-events-none opacity-50' : ''}`}
          >
            <Upload size={14} />
            {uploading ? 'Uploading…' : 'Upload document…'}
          </label>
          {uploadError && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
              {uploadError}
            </p>
          )}
        </div>

        {loadError && (
          <p role="alert" className="mb-3 text-sm text-[var(--color-status-destroyed)]">
            {loadError}
          </p>
        )}

        {documents.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No documents uploaded yet.</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
                <th className="py-2 pe-3 font-medium">File</th>
                <th className="py-2 pe-3 font-medium">Type</th>
                <th className="py-2 pe-3 font-medium">Size</th>
                <th className="py-2 pe-3 font-medium">Uploaded</th>
                <th className="py-2 pe-3 font-medium">Status</th>
                <th className="py-2 pe-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.document_id} className="border-b border-[var(--color-border)] align-top">
                  <td className="py-2 pe-3 text-[var(--color-text-primary)]">{doc.filename}</td>
                  <td className="py-2 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
                    {doc.file_type}
                  </td>
                  <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                    {formatSize(doc.size_bytes)}
                  </td>
                  <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                    {new Date(doc.uploaded_at).toLocaleString()}
                  </td>
                  <td className="py-2 pe-3">
                    <DocumentStatusChip status={doc.status} />
                    {doc.status === 'error' && doc.error_message && (
                      <div className="mt-1 text-xs text-[var(--color-status-destroyed)]">
                        {doc.error_message}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pe-3">
                    {doc.status === 'proposed' && (
                      <button
                        type="button"
                        onClick={() => setReviewDocId(doc.document_id)}
                        className="rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                      >
                        Review
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {reviewDocId !== null && reviewDoc && (
        <div className="hidden" data-testid="review-dialog-placeholder" />
        /* Replaced by DocumentReviewDialog in Task 11. */
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add the "Knowledge" tab to `LeftRail.tsx`**

In `frontend/src/components/layout/LeftRail.tsx`, add `FileText` to the lucide-react import:

```tsx
import { FileText, LayoutGrid, MapIcon, PlusCircle, RadioTower, Sparkles, Waypoints } from 'lucide-react'
```

Update both prop types (`page` and `onPageChange`'s parameter) from:

```tsx
  page: 'scope' | 'browse' | 'map' | 'feeds'
  onPageChange: (page: 'scope' | 'browse' | 'map' | 'feeds') => void
```

to:

```tsx
  page: 'scope' | 'browse' | 'map' | 'feeds' | 'knowledge'
  onPageChange: (page: 'scope' | 'browse' | 'map' | 'feeds' | 'knowledge') => void
```

Add a fifth tab entry to the tab array:

```tsx
        (
          [
            ['scope', Waypoints, 'Scope'],
            ['browse', LayoutGrid, 'Browse all'],
            ['map', MapIcon, 'Map'],
            ['feeds', RadioTower, 'Feeds'],
            ['knowledge', FileText, 'Knowledge'],
          ] as const
        ).map(([id, Icon, label]) => (
```

(Note the tab grid is `grid-cols-2` today — 5 tabs will wrap to a third row; this is a pure layout consequence of adding a tab, not something to fix here.)

- [ ] **Step 3: Wire the page into `App.tsx`**

In `frontend/src/App.tsx`, add the import:

```tsx
import { KnowledgeBasePage } from './components/knowledge/KnowledgeBasePage'
```

Update the page state type:

```tsx
  const [page, setPage] = useState<'scope' | 'browse' | 'map' | 'feeds' | 'knowledge'>('scope')
```

Update the canvas render ternary from:

```tsx
        canvas={
          page === 'browse' ? (
            <BrowsePage onSelectEntity={handleBrowseSelectEntity} />
          ) : page === 'map' ? (
            <MapPage onSelectEntity={handleMapSelectEntity} />
          ) : page === 'feeds' ? (
            <FeedsPage />
          ) : (
            canvasContent
          )
        }
```

to:

```tsx
        canvas={
          page === 'browse' ? (
            <BrowsePage onSelectEntity={handleBrowseSelectEntity} />
          ) : page === 'map' ? (
            <MapPage onSelectEntity={handleMapSelectEntity} />
          ) : page === 'feeds' ? (
            <FeedsPage />
          ) : page === 'knowledge' ? (
            <KnowledgeBasePage />
          ) : (
            canvasContent
          )
        }
```

- [ ] **Step 4: Manually verify**

Run: `cd frontend && npm run dev`, open the app, confirm a **Knowledge** tab appears in the left rail, clicking it shows the "Knowledge base" page with an **Upload document…** button and "No documents uploaded yet.", and that switching back to **Scope**/**Browse all**/**Map**/**Feeds** still works.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/knowledge/KnowledgeBasePage.tsx frontend/src/components/layout/LeftRail.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add Knowledge base page with upload and document list"
```

---

## Task 11: `DocumentReviewDialog` — wire review/commit into the page

**Files:**
- Create: `frontend/src/components/knowledge/DocumentReviewDialog.tsx`
- Modify: `frontend/src/components/knowledge/KnowledgeBasePage.tsx`

**Interfaces:**
- Consumes: `api.getBatch`, `api.commitBatch` (Task 8), `BatchReviewPanel` (Task 9).
- Produces: `<DocumentReviewDialog open batchId onOpenChange onCommitted />`.

- [ ] **Step 1: Create `DocumentReviewDialog.tsx`**

Create `frontend/src/components/knowledge/DocumentReviewDialog.tsx`:

```tsx
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { IngestBatch } from '../../lib/types'
import { BatchReviewPanel } from '../layout/BatchReviewPanel'
import { Button } from '../ui/Button'

interface DocumentReviewDialogProps {
  open: boolean
  batchId: string | null
  onOpenChange: (open: boolean) => void
  /** Called after a successful commit so the caller's document list can
   * pick up the new 'committed' status on its next poll. */
  onCommitted: () => void
}

type Phase = 'loading' | 'ready' | 'committing' | 'committed' | 'error'

/** Review/commit a batch produced by background document extraction — the
 * knowledge-base counterpart to IngestDialog's review step, except the
 * batch is fetched by id (GET /ingest/{batch_id}) instead of held in local
 * state from a just-finished extraction call. */
export function DocumentReviewDialog({
  open,
  batchId,
  onOpenChange,
  onCommitted,
}: DocumentReviewDialogProps) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [batch, setBatch] = useState<IngestBatch | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [commitError, setCommitError] = useState('')

  useEffect(() => {
    if (!open || !batchId) return
    setPhase('loading')
    setBatch(null)
    setErrorMessage('')
    setCommitError('')
    api
      .getBatch(batchId)
      .then((result) => {
        setBatch(result)
        setPhase('ready')
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setPhase('error')
      })
  }, [open, batchId])

  async function handleCommit() {
    if (!batch) return
    setPhase('committing')
    setCommitError('')
    try {
      await api.commitBatch(batch.batch_id)
      setPhase('committed')
      onCommitted()
    } catch (err) {
      setCommitError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('ready')
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Review extracted document
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {phase === 'loading' && <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>}

          {phase === 'error' && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
              {errorMessage}
            </p>
          )}

          {(phase === 'ready' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
            />
          )}

          {phase === 'committed' && (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-[var(--color-text-primary)]">
                Committed. Entities and links are now in the graph.
              </p>
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
```

- [ ] **Step 2: Wire it into `KnowledgeBasePage.tsx`**

In `frontend/src/components/knowledge/KnowledgeBasePage.tsx`, add the import:

```tsx
import { DocumentReviewDialog } from './DocumentReviewDialog'
```

Replace the placeholder block from Task 10 Step 1:

```tsx
      {reviewDocId !== null && reviewDoc && (
        <div className="hidden" data-testid="review-dialog-placeholder" />
        /* Replaced by DocumentReviewDialog in Task 11. */
      )}
```

with:

```tsx
      <DocumentReviewDialog
        open={reviewDocId !== null}
        batchId={reviewDoc?.batch_id ?? null}
        onOpenChange={(open) => !open && setReviewDocId(null)}
        onCommitted={() => {}}
      />
```

(`reviewDoc` is already computed earlier in the component from Task 10; `onCommitted` is a no-op because the page's existing 3-second poll picks up the `committed` status on its own — no explicit refresh call needed.)

- [ ] **Step 3: Manually verify the full upload → review → commit flow**

Run: `cd frontend && npm run dev` and, with the backend + Neo4j running (`docker compose up -d`, `npm run dev` at the repo root, or `uvicorn app.main:app` directly):

1. Go to the **Knowledge** tab, click **Upload document…**, pick a small `.txt` file with a sentence like "Major Ivan Petrov commands the 3rd Motor Rifle Battalion." — confirm a row appears immediately with status `processing`.
2. Wait a few seconds (poll interval is 3s) and confirm the row's status flips to `proposed` (or `error` with a message, if the LLM call fails — check `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` is set).
3. Click **Review** on the `proposed` row — confirm the dialog loads and shows the same entities/links/rejected table style as `IngestDialog`.
4. Click **Commit to graph** — confirm the dialog shows "Committed.", close it, and confirm the row's status is now `committed` (next poll).
5. Confirm the newly committed entity is visible via **Browse all** or by searching for it in **Scope**.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/knowledge/DocumentReviewDialog.tsx frontend/src/components/knowledge/KnowledgeBasePage.tsx
git commit -m "feat(frontend): add DocumentReviewDialog and wire it into the Knowledge base page"
```

---

## Task 12: Full-suite regression check

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && venv/bin/pytest -v`
Expected: PASS — every test across all files in `backend/tests/`, including all tests added in Tasks 1–7 and the pre-existing feed/entity/ontology/scoping suites.

- [ ] **Step 2: Run the frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 3: Re-run the manual end-to-end flow from Task 11 Step 3 once more, end to end**

Confirm: upload a `.pdf` (not just `.txt`, to exercise `pypdf`) with a couple of paragraphs, confirm it reaches `proposed`, review it, commit it, and confirm the committed entities appear in **Browse all**. This closes the loop on the one file type not otherwise manually exercised in Task 11.

- [ ] **Step 4: No commit for this task** — it's verification-only; if either step fails, fix the regression in the task that introduced it and re-commit there, not here.
