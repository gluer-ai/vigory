# Knowledge base: document upload & extraction — design

## Context

The app already has a text-ingestion pipeline: `POST /ingest` runs
`extract_from_text` (`backend/app/services/extraction_agent.py`), which calls
the configured LLM to extract entities/links from pasted text, validates
each against the Neo4j-stored ontology (`ClassDef`/`LinkDef`), and persists a
`proposed` `IngestBatch` node for human review. `POST
/ingest/{batch_id}/commit` merges that batch into the graph.
`IngestDialog.tsx` drives this end-to-end in the frontend (paste text →
extract → review table → commit).

The user wants a "knowledge base": upload PDF, Word, plain-text, Markdown,
and Excel files, and have the same entity/relationship extraction run
against their content. This is a browsable feature (uploaded documents
persist and can be revisited), not a one-shot paste-and-discard action like
today's text ingest.

Key decisions made during brainstorming:
- Uploaded files are **stored**, not just parsed-and-discarded — they form a
  browsable document library, each linked to the batch it produced.
- Storage is **local disk** under a configurable directory, no new external
  dependency. Production (Railway) needs a persistent volume mounted at that
  path — ephemeral by default otherwise. This is a deployment note, not
  something this spec's code needs to detect or enforce.
- Documents that exceed a single LLM call's usable context are **chunked**;
  chunk results are extracted independently, then **merged into one batch
  per document** (not one batch per chunk) so review/commit stays a single
  step per upload, matching today's UX.
- Excel files are **flattened to text** (sheet → row-by-row text) and run
  through the same free-text extraction prompt as prose documents — no
  separate structured/column-mapping path.
- Extraction runs **in the background** after upload returns (FastAPI
  `BackgroundTasks`, no new queue/worker infrastructure); the frontend polls
  document status until it's ready to review.

## Scope

**In scope:**
- `POST /documents/upload`: multipart upload of `.txt .md .pdf .docx .xlsx`,
  size-capped, stored on disk, returns immediately with
  `status=processing`.
- Text extraction per format (`pypdf`, `python-docx`, `openpyxl`, direct read
  for txt/md).
- Paragraph-boundary chunking for long documents.
- A small refactor of `extraction_agent.py` to expose a reusable
  extract-and-validate core (no batch persistence) callable per chunk,
  leaving the existing `extract_from_text` (used by `POST /ingest`)
  behaviorally unchanged.
- Cross-chunk entity dedup within one document: entities accepted from
  earlier chunks are added to later chunks' "existing entities" prompt
  context, the same mechanism already used for committed graph entities.
- Merging all chunks' results into a single `proposed` `IngestBatch` per
  document, reusing the existing commit endpoint unchanged.
- `GET /documents` (list) and `GET /documents/{id}` (poll target) endpoints.
- `GET /ingest/{batch_id}` (new — needed so the frontend can fetch a
  background-produced batch for review; today batches are only ever
  returned inline from the synchronous `POST /ingest` response).
- A new "Knowledge base" page in the frontend: upload control + document
  list with live status, review dialog reusing the existing review-table UI
  (extracted into a shared `BatchReviewPanel`), commit via the existing
  `commitBatch` call.
- Config: `upload_dir`, `max_upload_mb`, `ingest_chunk_chars` settings;
  `.env.example` documentation including the Railway-volume note.
- Tests for the new parser, chunker, and cross-chunk merge/dedup behavior,
  following the existing mocked-LLM style of `test_extraction_agent.py`.

**Explicitly out of scope (deferred or not planned):**
- Legacy binary `.doc`/`.xls` (pre-2007 Office formats) — only modern
  `.docx`/`.xlsx` (OOXML) are supported. `openpyxl` (already a dependency)
  cannot read binary `.xls`, and adding a second Excel-parsing library
  (`xlrd`) for an increasingly rare format isn't worth the extra
  dependency — `.xls` uploads are rejected by the extension allowlist with
  the same `400` as any other unsupported type.
- OCR for scanned/image-only PDFs. Text-layer PDFs only; a PDF with no
  extractable text yields an empty extraction (or a clear "no text found"
  error), not an image pipeline.
- Structured/column-mapped Excel extraction (deterministic row→entity
  mapping). Explicitly rejected in favor of flatten-to-text for v1.
- Object storage (S3-compatible) backend. Local disk + volume only.
- Editing or re-processing an already-uploaded document (delete/replace/
  re-extract). A document is uploaded once and produces one batch.
- Any change to how already-committed batches or the ontology validation
  logic work — this reuses that machinery as-is.
- Async task queue (Celery/Redis/etc.) — `BackgroundTasks` is sufficient for
  this workload and keeps the deploy footprint unchanged.

## Data model

New Neo4j node label `Document`:

| field | type | notes |
|---|---|---|
| `document_id` | str | `D-<uuid8>`, primary key |
| `filename` | str | original filename as uploaded |
| `file_type` | str | `txt`\|`md`\|`pdf`\|`docx`\|`xlsx` |
| `size_bytes` | int | |
| `file_path` | str | path on disk, relative to `upload_dir` |
| `status` | str | `processing`\|`proposed`\|`committed`\|`error` |
| `error_message` | str\|null | set only when `status=error` |
| `batch_id` | str\|null | set once extraction produces a batch |
| `uploaded_at` | str (ISO 8601) | |

`IngestBatch` gains one new optional property: `document_id` (str\|null),
set for batches produced via upload, left unset for today's paste-text
batches. `commit_batch` needs no change — committing a document-sourced
batch behaves identically to committing a text-sourced one; `Document.status`
becomes `committed` as a side effect of a successful commit (the commit
endpoint looks up `document_id` on the batch and, if present, updates the
matching `Document` node in the same transaction).

## Architecture

### Backend

`backend/app/services/document_parser.py` (new):
```
extract_text(filename: str, content: bytes) -> str
```
Dispatches on file extension:
- `.txt`, `.md` → UTF-8 decode.
- `.pdf` → `pypdf.PdfReader`, concatenate each page's `.extract_text()`.
- `.docx` → `python-docx`, concatenate paragraph text (and table cell text).
- `.xlsx` → `openpyxl` (`data_only=True`), for each sheet emit a
  `"# Sheet: <name>"` header then one line per row with cells joined by
  `" | "` (header row included as-is — the LLM infers column meaning from
  context, same as it infers entity roles from prose).

Unsupported/unparseable content raises `ValueError` with a message surfaced
as the document's `error_message`.

`backend/app/services/chunking.py` (new):
```
chunk_text(text: str, max_chars: int = settings.ingest_chunk_chars) -> list[str]
```
Greedily packs paragraphs (split on blank lines) into chunks up to
`max_chars`; a single paragraph longer than `max_chars` is hard-split rather
than dropped. Returns `[text]` unchanged (one chunk) when the whole document
already fits.

`extraction_agent.py` refactor: the existing `extract_from_text` body from
"call the LLM" through "build `valid_entities`/`rejected_entities`/
`valid_links`/`rejected_links`" is extracted into:
```
async def _extract_and_validate(
    session, text: str, extra_existing_entities: list[dict] | None = None,
) -> dict  # {"valid_entities", "rejected_entities", "valid_links", "rejected_links"}
```
`extra_existing_entities` is appended to the `EXISTING_ENTITIES` prompt
section alongside the graph-sourced list — this is the cross-chunk dedup
hook. `extract_from_text` becomes a thin wrapper: call
`_extract_and_validate` with `extra_existing_entities=None`, persist the
`IngestBatch` exactly as it does today. No behavior change for `POST
/ingest`.

`backend/app/services/document_ingest.py` (new) — the orchestrator:
```
async def save_upload(session, filename: str, content: bytes) -> dict  # Document
```
Validates extension/size, writes `content` to
`{upload_dir}/{document_id}/{filename}`, creates the `Document` node with
`status="processing"`, returns it. Called synchronously from the upload
endpoint (fast: just a disk write + one Neo4j write).

```
async def process_document(document_id: str) -> None
```
Runs as a `BackgroundTasks` job (opens its own Neo4j session — it outlives
the request). Steps: load the `Document`, read the file from disk,
`extract_text`, `chunk_text`, then loop chunks sequentially, calling
`_extract_and_validate` per chunk with the running list of this document's
own accepted entities as `extra_existing_entities`. Concatenates all chunks'
valid/rejected entities and links, de-duplicating entities by `entity_id`
(later chunks that correctly reused an earlier chunk's id are already
collapsed by construction, not by post-hoc dedup). Persists one
`IngestBatch` (`document_id` set), then updates `Document.status="proposed"`,
`Document.batch_id`. Any exception along the way sets `Document.status="error"`,
`Document.error_message=str(exc)` instead of propagating (this runs
detached from the request, so there's no client to raise an HTTP error to).

### API (`backend/app/api/documents.py`, new router, prefix `/documents`)

- `POST /documents/upload` — multipart `UploadFile`; calls `save_upload`,
  schedules `process_document` via `BackgroundTasks`, returns the `Document`
  (status `processing`) with `202`-equivalent semantics (FastAPI default
  `200` is fine — status is communicated via the body, matching this
  codebase's existing convention of not leaning on HTTP status semantics
  beyond errors).
- `GET /documents` — list, newest `uploaded_at` first.
- `GET /documents/{document_id}` — single document (poll target).
- `GET /documents/{document_id}/file` — streams the original file back
  (`FileResponse`), for the "browsable" download case.

### API (`backend/app/api/ingest.py`, extended)

- `GET /ingest/{batch_id}` — new; returns the same shape as today's `POST
  /ingest` response (`batch_id`, `status`, `entities`, `links`,
  `rejected_entities`, `rejected_links`), read via the same `MATCH
  (b:IngestBatch {batch_id: $id})` lookup `commit_batch` already does. 404
  if not found.
- `POST /ingest/{batch_id}/commit` — unchanged logic, plus: after setting
  `b.status = 'committed'`, if the batch has a `document_id`, also set the
  matching `Document.status = 'committed'` in the same session.

### Frontend

`LeftRail`'s page tabs (`scope | browse | map | feeds`) gain `knowledge`
("Knowledge base"). `App.tsx`'s `page` union type and router-less page-switch
(`{page === 'x' && <XPage />}`) pattern extends the same way `feeds` did.

`frontend/src/components/knowledge/KnowledgeBasePage.tsx` (new), structurally
parallel to `FeedsPage.tsx`:
- Polls `GET /documents` on a fixed interval (matching `FeedsPage`'s
  `REFRESH_MS = 5000`) — no conditional start/stop logic, kept simple like
  the existing feeds page.
- A file input (`accept=".txt,.md,.pdf,.docx,.xlsx"`) styled as a
  button; on change, calls `api.uploadDocument(file)` and optimistically
  prepends the returned `processing` row.
- A table: filename, type, size, uploaded-at, status chip. `error` rows show
  `error_message` inline (same red text pattern used elsewhere, e.g.
  `IngestDialog`'s `role="alert"` text). `proposed` rows get a **Review**
  button.

`frontend/src/components/layout/BatchReviewPanel.tsx` (new) — the
entities/links/rejected `ResultTable`s + commit button, extracted verbatim
out of `IngestDialog.tsx`'s `review`/`committing` phase block, parameterized
by `batch`, `onCommit`, `committing`, `commitError`. `IngestDialog.tsx` is
updated to render this shared component instead of its inline markup (pure
extraction, no behavior change).

`frontend/src/components/knowledge/DocumentReviewDialog.tsx` (new) — opened
from a document's **Review** button; on open, calls `api.getBatch(batch_id)`
(new `GET /ingest/{id}` endpoint), then renders `BatchReviewPanel`; commit
calls the existing `api.commitBatch`, and on success closes and lets the
page's next poll pick up the `committed` status.

`lib/api.ts`:
- A new `requestForm<T>(path, formData)` helper alongside `request` — same
  error handling, but does **not** set `content-type` (the browser sets the
  multipart boundary itself; the existing `request` unconditionally forces
  `application/json`, which would corrupt a `FormData` upload).
- `uploadDocument(file: File)` → `requestForm<Document>('/documents/upload', form)`.
- `listDocuments()` → `GET /documents`.
- `getDocument(id)` → `GET /documents/{id}`.
- `getBatch(id)` → `GET /ingest/{id}`.

`lib/types.ts`: add `Document` type mirroring the backend node fields above.

## Data flow

Upload: `KnowledgeBasePage` → `api.uploadDocument` → `POST
/documents/upload` → `save_upload` (disk write + `Document` node, status
`processing`) → response returned to the browser → `BackgroundTasks`
schedules `process_document` server-side, decoupled from the request/response
cycle. `KnowledgeBasePage`'s next poll (`GET /documents`) picks up whatever
status the background job has reached — `processing` → `proposed` or
`error` — no explicit "job done" push/websocket needed.

Review: user clicks **Review** on a `proposed` row → `DocumentReviewDialog`
opens → `GET /ingest/{batch_id}` → `BatchReviewPanel` renders → **Commit** →
`POST /ingest/{batch_id}/commit` (existing endpoint, now also flips the
`Document` to `committed`) → dialog closes → next page poll shows
`committed`.

Text-paste ingest (`IngestDialog`) is untouched end-to-end; it never touches
`Document` nodes or the new endpoints.

## Error handling

- Upload validation (bad extension, over size cap) is synchronous — `POST
  /documents/upload` returns `400` immediately, no `Document` node created,
  matching how other malformed-request cases in this API respond today.
- Parse/extraction failures happen in the detached background job, so they
  can't raise an HTTP error — they're captured into
  `Document.status="error"` / `error_message`, surfaced by the next poll as
  an inline error on that row (no toast/notification system exists in this
  app to push it proactively).
- `GET /ingest/{batch_id}` on an unknown id → `404`, same convention as the
  existing `commit_batch` 404.
- A chunk-level LLM failure (`LLMError`, e.g. rate limit) aborts the whole
  document's processing (`status="error"`) rather than partially committing
  some chunks' results — a document's batch is all-or-nothing to review,
  matching the "merge into one batch" decision; partial success would need
  its own UI treatment this spec doesn't build.

## Testing

Backend (pytest, following `test_extraction_agent.py`'s mocked-LLM
convention):
- `test_document_parser.py` — one fixture file per format, assert extracted
  text contains expected content; a corrupt/empty file raises `ValueError`.
- `test_chunking.py` — chunk-boundary behavior: under-limit text stays one
  chunk, over-limit text splits on paragraph boundaries, a single oversized
  paragraph still gets split rather than dropped.
- `test_document_ingest.py` — `process_document` over a mocked multi-chunk
  document with a mocked LLM: asserts a single merged `IngestBatch` is
  created (not one per chunk), and that an entity mocked-returned identically
  in two chunks collapses into one `valid_entities` row (the cross-chunk
  dedup path).
- Extend `test_extraction_agent.py` minimally to cover the
  `_extract_and_validate`/`extract_from_text` split still produces identical
  output for the existing test cases (regression guard on the refactor).

Frontend: no existing test suite was found for dialogs/pages beyond
`InteractiveMap.test.tsx`'s Cesium mock (per the prior spec) — this feature
follows the same manual-verification approach already used for
`IngestDialog`/`FeedsPage`, exercised via the running dev app rather than
new component tests, consistent with current project practice.

## Dependencies

New backend dependencies (add to `backend/requirements.txt`):
- `pypdf` — PDF text extraction, pure Python, no system-level deps.
- `python-docx` — DOCX text/table extraction.

`openpyxl` is already a dependency (used by `ontology/import_ontology.py`).
No new frontend dependencies — file upload uses the native `<input
type="file">` + `fetch`/`FormData`, no library needed.

## Risks

- **`.xls` not supported**: only modern `.docx`/`.xlsx` are accepted (see
  Scope). If legacy `.xls`/`.doc` files turn out to matter in practice, that
  needs its own follow-up (an `xlrd`/legacy-`.doc` parsing path), not
  addressed here.
- **Sequential chunk processing cost**: a very large document (many chunks)
  means many sequential LLM calls in the background job — slower wall-clock
  time to `proposed`, and proportionally higher LLM cost per upload than a
  single paste. No parallelization is planned for v1 (sequential is also
  what makes the cross-chunk dedup context-passing simple); a size/chunk-count
  ceiling via `max_upload_mb` is the only guardrail.
- **`BackgroundTasks` lifetime**: FastAPI `BackgroundTasks` run in the same
  process/worker as the request; if the backend restarts (deploy) mid-job, a
  `processing` document is left stuck with no automatic retry. Acceptable
  for v1 (matches this app's general lack of job-recovery infrastructure
  elsewhere, e.g. feed polling); not solved here.
