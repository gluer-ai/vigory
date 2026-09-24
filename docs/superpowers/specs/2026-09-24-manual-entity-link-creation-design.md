# Manual entity/link creation with synonym detection — design

## Context

`extraction_agent.py`'s `PROMPT_TEMPLATE` constrains the LLM to the
Neo4j-stored military-intelligence ontology (`ClassDef`/`LinkDef`, seeded
from `ontology/milinteltaxonomyontology.xlsx`) and explicitly instructs it
to omit anything that doesn't cleanly match a leaf class or link type
("If nothing in the list fits, omit that entity/link rather than
guessing."). For an out-of-domain document (e.g. a generic business
proposal with no clearly-typed named entities), this correctly produces an
empty `entities`/`links` result — not a bug, but it leaves the reviewer
with no way forward: `BatchReviewPanel`'s Commit button is disabled when
both arrays are empty (`batch.entities.length === 0 && batch.links.length
=== 0`), and neither `commit_batch` nor any other endpoint supports adding
to a `proposed` `IngestBatch` before commit.

Separately, there is no synonym-aware entity resolution anywhere in the
app. `search_entities_by_name` (`app/services/search.py`) is a
case-insensitive substring match on label/aliases — it would not connect
"Automobile" to an existing "Car" entity, since the strings share no
substring. The extraction agent's own dedup (reusing an `EXISTING_ENTITIES`
entity_id for "same name, or an alias/clear variant") only applies to
LLM-driven extraction, not to anything created by hand.

The app already has a full manual entity/link creation UI —
`AddResourceDialog.tsx` (`EntityForm`/`LinkForm`) — but it targets the
direct-to-graph `POST /entities` / `POST /links` endpoints, bypassing the
`IngestBatch` propose-then-commit review flow entirely. This spec adds a
**second** manual-creation path scoped to a batch under review, so an
extraction that came back empty (or incomplete) still has a path into the
graph without skipping review, plus a synonym check on that path so
"Automobile" and "Car" resolve to one entity instead of two.

Key decisions made during brainstorming:
- Manual creation lives **inside batch review only** — not a general
  "Entities" page capability. `POST /entities` / `POST /links` (direct,
  no review step) are untouched and out of scope.
- Synonym detection is an **LLM semantic check**, reusing the same
  `complete_json` client the extraction agent already uses — fuzzy string
  matching was considered and rejected because it cannot catch lexically
  unrelated synonyms like "Automobile"/"Car".
  matching candidates.
- A detected match is **always surfaced for confirmation**, never
  auto-merged — the user picks "use existing" or "create new anyway".
  This matches the app's existing "identity is a hypothesis" principle
  (see `extraction_agent.py`'s module docstring): nothing merges without a
  human decision.
- The check-then-create flow is **two separate endpoints** (a read-only
  `GET .../suggest` and a `POST .../entities` that always creates), not one
  endpoint with a `force_new` flag — a GET that never mutates and a POST
  that always does is a more honest interface than a POST that sometimes
  doesn't create.
- `EntityForm`/`LinkForm` are **refactored to accept an `onSubmit` prop**
  rather than duplicated, so batch-scoped creation and the existing
  direct-to-graph `AddResourceDialog` share one implementation.

## Scope

**In scope:**
- `GET /ingest/{batch_id}/entities/suggest` — read-only synonym check
  against same-`entity_class` candidates (committed graph entities + the
  batch's own staged entities).
- `POST /ingest/{batch_id}/entities` — adds a client-specified
  `EntityCreate` to a `proposed` batch's `entities` array, after the same
  `validate_entity` ontology check extraction already runs.
- `POST /ingest/{batch_id}/links` — adds a client-specified `LinkCreate` to
  a `proposed` batch's `links` array, after `validate_link`, resolving
  endpoint classes from the batch's own entities first, then falling back
  to already-committed `Entity` nodes.
- `backend/app/services/entity_resolution.py` (new) — `find_synonym_match`,
  the LLM-backed candidate lookup used by the suggest endpoint.
- Frontend: `EntityForm`/`LinkForm` refactor (`onSubmit` prop extraction,
  behavior-preserving for `AddResourceDialog`), a new synonym-confirmation
  banner in `EntityForm` (only active when a `onCheckSynonym` prop is
  passed), "+ Add entity" / "+ Add link" affordances in `BatchReviewPanel`,
  and a source/target entity autocomplete in the link form reusing the
  existing `api.searchEntities`.
- Tests for the two new endpoints and `find_synonym_match`.

**Explicitly out of scope (deferred or not planned):**
- Editing or removing an entity/link already added to a batch. If a
  mistake is made, existing recourse (abandon the batch / re-ingest)
  is unchanged. Flagged as a candidate follow-up, not built here.
- Any change to `POST /entities` / `POST /links` (direct-to-graph
  creation) or to `AddResourceDialog`'s existing behavior — this reuses
  its form components but does not alter its current flow.
- Synonym detection during LLM extraction itself. Cross-chunk/cross-batch
  reuse there is already handled by the existing `EXISTING_ENTITIES`
  prompt mechanism and is unrelated to this manual-creation path.
- Concurrency control on simultaneous edits to the same batch. Read-modify-
  write on the batch's JSON properties, unguarded — acceptable at this
  app's single-reviewer, demo scale (same assumption `document_ingest.py`
  already documents for its own chunk loop).
- Any UI outside `BatchReviewPanel`'s consumers (`IngestDialog`,
  `DocumentReviewDialog`).

## Data model

No new node labels or schema changes. The batch-add endpoints accept the
existing `EntityCreate`/`LinkCreate` models unchanged (`app/models/entity.py`,
`app/models/link.py`) — client-supplied `entity_id`/`link_id`, matching how
`POST /entities`/`POST /links` already work. `IngestBatch.entities`/`.links`
(JSON-encoded string properties) gain rows via read-modify-write, same
storage shape `commit_batch` already reads.

## Architecture

### Backend

`backend/app/services/entity_resolution.py` (new):
```
async def find_synonym_match(
    session, label: str, entity_class: str, aliases: list[str],
    batch_entities: list[dict],
) -> dict | None
```
Candidates = committed `Entity` nodes with `entity_class = $entity_class`
(capped at 200, same limit pattern as `_fetch_existing_entities`) plus
`batch_entities` filtered to the same `entity_class`. Empty candidate list
→ return `None` immediately, no LLM call. Otherwise calls `complete_json`
with a dedicated prompt (distinct from `extraction_agent.PROMPT_TEMPLATE`):
gives the new label + aliases and each candidate as
`entity_id | label (aliases)`, asks whether the new entity is the same
real-world thing or a synonym/alias for the same thing as one candidate —
using "Automobile"/"Car" as the illustrative case of generic-term
synonymy, not just exact-name matching. Response contract:
`{"match_entity_id": "<id>" | null, "reason": "<one sentence>"}`. A
`match_entity_id` outside the candidate set (hallucination) is treated as
no match, same defensive pattern as the inverse-link-type guard in
`_extract_and_validate`. `LLMError` propagates uncaught — the caller
decides how to surface it.

### API (`backend/app/api/ingest.py`, extended)

- `GET /ingest/{batch_id}/entities/suggest?label=&entity_class=&aliases=`
  — loads the batch (404 if missing), calls `find_synonym_match`, returns
  `{"match": {entity_id, label, aliases, entity_class, entity_subclass} |
  null, "reason": str | null}`. `LLMError` → `502`, same convention as
  `POST /ingest`.
- `POST /ingest/{batch_id}/entities` — body `EntityCreate`. 404 unknown
  batch; 409 if `batch.status != "proposed"` (matches `commit_batch`'s
  guard) or if `entity_id` collides with an id already in the batch or an
  existing committed `Entity`; 422 via `validate_entity` on an invalid
  `entity_subclass`. On success, appends to `entities`, persists via `SET
  b.entities = $entities`, returns the full updated batch (same shape as
  `GET /ingest/{batch_id}`).
- `POST /ingest/{batch_id}/links` — body `LinkCreate`. Same 404/409-status
  guard and `link_id` collision check as above. Resolves
  `source_entity`/`target_entity` classes by checking the batch's own
  `entities` first, then `MATCH (e:Entity {entity_id: $id})` for
  already-committed entities (so a link can target an entity the reviewer
  chose to reuse via "suggest" rather than create); 422 if either endpoint
  resolves to neither, reusing `_extract_and_validate`'s existing "not in
  this batch's valid set and not an existing entity_id" message; 422 via
  `validate_link` on a domain/range mismatch. Appends to `links`, persists,
  returns the full updated batch.

### Frontend

`AddResourceDialog.tsx` refactor:
- `EntityForm`/`LinkForm` take an `onSubmit: (payload) => Promise<{id:
  string}>` prop instead of calling `api.createEntity`/`api.createLink`
  internally. `AddResourceDialog`'s existing tab wiring passes those same
  calls, so its direct-to-graph behavior is unchanged.
- `EntityForm` gains two optional props: `onCheckSynonym?: (label,
  entityClass, aliases) => Promise<SynonymMatch | null>` and
  `onUseExisting?: (entityId: string) => void`. On submit, if
  `onCheckSynonym` is provided, it runs first. A match renders an inline
  banner — *"Looks like an existing entity: **Car**
  (VEHICLE.CIVILIAN_CAR). {reason}"* — with **Use existing** (calls
  `onUseExisting`, nothing is created) and **Create new anyway** (proceeds
  to `onSubmit`, skipping the check on that attempt). No match submits
  straight through — zero extra clicks in the common case. If
  `onCheckSynonym`'s promise rejects (suggest endpoint down), the form
  shows a small dismissible "couldn't check for duplicates" note and calls
  `onSubmit` directly rather than blocking. Neither prop is passed from
  `AddResourceDialog`, so its flow never triggers this path.

`BatchReviewPanel.tsx`:
- New `onBatchUpdated: (batch: IngestBatch) => void` prop. `IngestDialog`/
  `DocumentReviewDialog` (which already hold `batch` in state) pass their
  existing setters through.
- "+ Add entity" / "+ Add link" buttons next to each `ResultTable`
  header, opening `EntityForm`/`LinkForm` (reused from
  `AddResourceDialog`) wired to:
  - entity: `onCheckSynonym={(l, c, a) => api.suggestBatchEntity(batch.batch_id, l, c, a)}`,
    `onSubmit={(e) => api.addBatchEntity(batch.batch_id, e)}`,
    `onUseExisting={(id) => /* record id as available for link source/target, no batch mutation */}`.
  - link: `onSubmit={(l) => api.addBatchLink(batch.batch_id, l)}`. Source/
    target inputs upgrade from plain text to an autocomplete combining the
    batch's own entities with `api.searchEntities` (existing endpoint), so
    a link can target a freshly-added entity, an "used existing" match, or
    any other already-committed entity.
- Both `onSubmit` paths call `onBatchUpdated` with the endpoint's returned
  batch. The Commit button's existing disabled check
  (`entities.length === 0 && links.length === 0`) needs no code change —
  it unblocks naturally once something is added.

`lib/api.ts`:
- `suggestBatchEntity(batchId, label, entityClass, aliases)` → `GET
  /ingest/{batchId}/entities/suggest?...`.
- `addBatchEntity(batchId, entity: EntityCreateInput)` → `POST
  /ingest/{batchId}/entities`.
- `addBatchLink(batchId, link: LinkCreateInput)` → `POST
  /ingest/{batchId}/links`.

`lib/types.ts`: add a `SynonymMatch` type (`entity_id`, `label`, `aliases`,
`entity_class`, `entity_subclass`, `reason`) for the suggest response.

## Data flow

Add entity (no match): user opens "+ Add entity" in `BatchReviewPanel` →
fills `EntityForm` → submit → `onCheckSynonym` → `GET .../suggest` returns
`{match: null}` → form calls `onSubmit` → `POST .../entities` → batch
persisted with the new entity → `onBatchUpdated` updates the parent's
`batch` state → `ResultTable` re-renders with the new row → Commit
un-disables if this was the first entity/link.

Add entity (match found): same up to `GET .../suggest`, which returns a
candidate → banner shown → **Use existing**: `onUseExisting(match.entity_id)`
closes the form, no backend write; that id becomes usable in the link
form's source/target autocomplete (already covers non-batch entities via
`api.searchEntities`, so no special-casing needed) → **Create new anyway**:
form calls `onSubmit` directly, same as the no-match path.

Add link: user opens "+ Add link" → picks source/target from the
autocomplete (batch entities ∪ `api.searchEntities` results) and a link
type → submit → `POST .../links` → `onBatchUpdated`.

Direct-to-graph creation (`AddResourceDialog`, opened elsewhere in the app)
is untouched end-to-end — it never calls the new batch-scoped endpoints or
the synonym check.

## Error handling

- `batch.status != "proposed"` → `409` on both new POST endpoints, same
  convention as `commit_batch`.
- `entity_id`/`link_id` collision (batch-local or already-committed) →
  `409`, mirroring `POST /entities`'s existing check.
- Invalid `entity_subclass` / link domain-range mismatch → `422` via the
  existing `validate_entity`/`validate_link` — manual creation cannot
  bypass the ontology.
- Link endpoint not resolvable to any known entity → `422`, reusing
  `_extract_and_validate`'s existing message for the same condition.
- Synonym-check LLM failure → `502` from `GET .../suggest`; the frontend
  treats this as "skip the check" rather than blocking entity creation.
- LLM hallucinating a `match_entity_id` outside the candidate set is
  treated as no match, not surfaced as an error.
- Editing/removing a mistakenly-added batch entity/link is unsupported
  (see Scope) — not an error case this spec handles, a known gap.

## Testing

Backend (pytest, extending `test_ingest_api.py`'s faked-Neo4j-session
style):
- `POST /ingest/{batch_id}/entities`: happy path; 404 unknown batch; 409
  non-`proposed` batch; 409 `entity_id` collision (batch-local and
  committed-graph cases); 422 invalid `entity_subclass`.
- `POST /ingest/{batch_id}/links`: happy path referencing a batch entity;
  happy path referencing an already-committed entity; 422 unknown
  endpoint reference; 422 domain/range mismatch; 409 status/collision
  cases mirroring the entity endpoint.
- `GET /ingest/{batch_id}/entities/suggest`: no-match case; match case;
  `502` on `LLMError`.
- New `test_entity_resolution.py`: `find_synonym_match` with a mocked
  `complete_json` — zero candidates (asserts the LLM is never called), a
  real match, a hallucinated id (→ `None`), an `LLMError` propagating.

Frontend: no established component-test convention exists for dialogs/
pages beyond `InteractiveMap.test.tsx`'s Cesium mock — verified manually
against the dev server (upload a document that extracts nothing, confirm
the add-entity/add-link controls appear and the Commit button unblocks;
deliberately add a label that's a known synonym of an existing entity and
confirm the banner appears and both "use existing"/"create new anyway"
paths work), consistent with current project practice.

## Dependencies

None new. Reuses `app/llm/client.py` (`complete_json`, already a
dependency for extraction), existing Neo4j driver, existing frontend
libraries (`@radix-ui/react-dialog`, `@radix-ui/react-tabs` already used by
`AddResourceDialog`).

## Risks

- **LLM cost/latency per manual add**: every entity creation triggers a
  `GET .../suggest` call (unless the candidate list is empty) — one extra
  LLM round-trip per manual entity, on top of extraction's existing calls.
  Acceptable given this is a human-paced review action, not a bulk path.
- **Candidate cap (200) same-class entities**: if a single `entity_class`
  grows past 200 committed entities, the synonym check only sees the
  first 200 (ordered by `entity_id`, same as `_fetch_existing_entities`)
  — a real duplicate outside that window could be missed. Matches the
  existing limitation already accepted for extraction's own
  `EXISTING_ENTITIES` context; not solved differently here.
- **No edit/remove on batch-staged additions**: a typo'd manual entity
  can't be fixed in place (see Scope). Low risk at demo scale but a real
  rough edge if this sees heavier use — flagged as a likely follow-up.
