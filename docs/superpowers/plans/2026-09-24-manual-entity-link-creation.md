# Manual Entity/Link Creation With Synonym Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer manually add an entity or link to a `proposed` `IngestBatch` (so a document/scenario that extracted nothing still has a path into the graph), with an LLM-backed synonym check on entity creation so a near-duplicate label (e.g. "Automobile" vs "Car") gets surfaced for reuse instead of silently creating a second entity.

**Architecture:** Two new endpoints on the existing `/ingest` router (`GET .../entities/suggest`, `POST .../entities`) plus one more (`POST .../links`), all operating on the `IngestBatch` node's JSON-encoded `entities`/`links` properties the same way `commit_batch` already reads them. A new `backend/app/services/entity_resolution.py` does the LLM synonym lookup, reusing `app/llm/client.py`. On the frontend, the existing `AddResourceDialog.tsx` (`EntityForm`/`LinkForm`, today wired to the direct-to-graph `POST /entities`/`POST /links`) is refactored to accept an `onSubmit` prop so a new `AddToBatchDialog.tsx` can reuse the same forms against the batch-scoped endpoints, plumbed into `BatchReviewPanel.tsx`.

**Tech Stack:** FastAPI + Pydantic v2 + async `neo4j` driver (backend), React + Vite + Tailwind + Radix UI (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-24-manual-entity-link-creation-design.md`

## Global Constraints

- Manual creation is scoped to batch review only — `POST /entities`, `POST /links` (direct-to-graph, `backend/app/api/entities.py`/`links.py`) and `AddResourceDialog`'s existing behavior must not change.
- Batch-scoped adds require `batch["status"] == "proposed"` — `409` otherwise, same convention as `commit_batch`.
- `entity_id`/`link_id` collisions are checked against both the batch's own staged rows and the already-committed graph — `409` on either.
- All manually-added entities/links still go through `validate_entity`/`validate_link` (`app/ontology/validate.py`) — no ontology bypass, `422` on failure.
- The synonym check is LLM-based (reuses `complete_json` from `app/llm/client.py`) and never auto-merges — a match is always surfaced for the user to accept ("use existing") or dismiss ("create new anyway").
- Backend tests use the `FakeSession`/no-real-DB/no-real-LLM convention already established in `backend/tests/test_ingest_api.py` and `backend/tests/test_extraction_agent.py`.
- Frontend has no automated component-test suite beyond `InteractiveMap.test.tsx` — new UI is verified manually via `npm run dev`; each frontend task's automated check is `npm run test:frontend` (`cd frontend && npx tsc -b --noEmit`).
- Backend tests run via `npm run test:backend` (`cd backend && ./venv/bin/pytest -q`); individual files via `cd backend && venv/bin/pytest tests/test_x.py -v`.

---

## Task 1: Synonym-check service (`entity_resolution.py`)

**Files:**
- Create: `backend/app/services/entity_resolution.py`
- Test: `backend/tests/test_entity_resolution.py`

**Interfaces:**
- Produces: `async def find_synonym_match(session, label: str, entity_class: str, aliases: list[str], batch_entities: list[dict]) -> dict | None`. On a match, returns `{"entity_id": str, "label": str, "aliases": list[str], "entity_class": str, "entity_subclass": str, "reason": str}`. Raises `app.llm.client.LLMError` if the LLM call fails. Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_entity_resolution.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_entity_resolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.entity_resolution'`.

- [ ] **Step 3: Implement `entity_resolution.py`**

Create `backend/app/services/entity_resolution.py`:

```python
"""Synonym/duplicate check for manually-created entities: before a new
entity is added to a proposed batch, ask the LLM whether its label is a
synonym or the same real-world thing as an existing entity of the same
class, so 'Automobile' and 'Car' resolve to one entity instead of two.
"""
from neo4j import AsyncSession

from app.llm.client import complete_json

_CANDIDATE_LIMIT = 200

SYNONYM_PROMPT_TEMPLATE = """You are deduplicating entities in an intelligence graph. A new \
entity is about to be created. Decide whether it refers to the SAME real-world thing as one \
of the existing entities below, OR whether its label is a synonym/alias/generic-vs-specific \
variant of the same thing an existing entity already represents — for example "Automobile" \
and "Car" are synonyms of the same kind of thing, "UN" and "United Nations" are the same \
organization. If genuinely different, even if related or similar-sounding, do not match.

EXISTING_ENTITIES — each line is "entity_id | label (aliases)":
{candidates}

Return strict JSON: {{"match_entity_id": "<id>" or null, "reason": "<one sentence>"}}. Return \
JSON only, no prose."""


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        aliases = f" ({', '.join(c['aliases'])})" if c.get("aliases") else ""
        lines.append(f"{c['entity_id']} | {c['label']}{aliases}")
    return "\n".join(lines)


async def find_synonym_match(
    session: AsyncSession,
    label: str,
    entity_class: str,
    aliases: list[str],
    batch_entities: list[dict],
) -> dict | None:
    """Look for an existing entity — committed, or already staged in this
    batch — of the same entity_class that `label` is a synonym/duplicate
    of. Returns the matching candidate (plus a "reason" key), or None if
    there are no candidates or none match. Propagates LLMError untouched.
    """
    result = await session.run(
        """
        MATCH (e:Entity)
        WHERE e.entity_class = $entity_class
        RETURN e.entity_id AS entity_id, e.label AS label, e.aliases AS aliases,
               e.entity_subclass AS entity_subclass
        ORDER BY e.entity_id
        LIMIT $limit
        """,
        entity_class=entity_class,
        limit=_CANDIDATE_LIMIT,
    )
    candidates = [dict(record) async for record in result]
    candidates += [
        {
            "entity_id": e["entity_id"],
            "label": e["label"],
            "aliases": e.get("aliases", []),
            "entity_subclass": e.get("entity_subclass", ""),
        }
        for e in batch_entities
        if e.get("entity_class") == entity_class
    ]
    if not candidates:
        return None

    system_prompt = SYNONYM_PROMPT_TEMPLATE.format(candidates=_format_candidates(candidates))
    user_prompt = f"New entity: {label} (aliases: {', '.join(aliases) or 'none'})"
    raw = await complete_json(system_prompt, user_prompt)

    match_id = raw.get("match_entity_id")
    if not match_id:
        return None
    match = next((c for c in candidates if c["entity_id"] == match_id), None)
    if match is None:
        return None
    return {**match, "entity_class": entity_class, "reason": raw.get("reason", "")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_entity_resolution.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/entity_resolution.py backend/tests/test_entity_resolution.py
git commit -m "feat(backend): add LLM-backed entity synonym/duplicate check"
```

---

## Task 2: `GET .../entities/suggest` and `POST .../entities`

**Files:**
- Modify: `backend/app/api/ingest.py`
- Test: `backend/tests/test_ingest_api.py`

**Interfaces:**
- Consumes: Task 1's `find_synonym_match(session, label, entity_class, aliases, batch_entities) -> dict | None`.
- Produces: `_get_batch(session, batch_id) -> dict` (404s via `HTTPException` if missing), `_require_proposed(batch) -> None` (409s if not `"proposed"`), `_batch_response(batch) -> dict` (same shape `GET /ingest/{batch_id}` already returns) — all three consumed by Task 3. Routes: `GET /ingest/{batch_id}/entities/suggest`, `POST /ingest/{batch_id}/entities`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ingest_api.py` (after the existing four tests, before nothing else — it's the last thing in the file). First, replace the `_FakeSession` class (it needs ontology/entity surface the new endpoints exercise) — find this block:

```python
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
```

and replace it with:

```python
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
```

Then append these tests to the end of the file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: FAIL — the new tests 404/error because the routes don't exist yet (and the pre-existing 4 tests should still PASS, since the `_FakeSession` replacement is additive).

- [ ] **Step 3: Implement the endpoints**

Replace the full contents of `backend/app/api/ingest.py` with:

```python
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.ontology.validate import ValidationError, validate_entity
from app.services.entity_resolution import find_synonym_match
from app.services.extraction_agent import extract_from_text

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    text: str


async def _get_batch(session, batch_id: str) -> dict:
    result = await session.run(
        "MATCH (b:IngestBatch {batch_id: $id}) RETURN b", id=batch_id
    )
    record = await result.single()
    if record is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return dict(record["b"])


def _require_proposed(batch: dict) -> None:
    if batch["status"] != "proposed":
        raise HTTPException(status_code=409, detail=f"batch already {batch['status']}")


def _batch_response(batch: dict) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "source_text": batch.get("source_text", ""),
        "entities": json.loads(batch["entities"]),
        "links": json.loads(batch["links"]),
        "rejected_entities": json.loads(batch.get("rejected_entities") or "[]"),
        "rejected_links": json.loads(batch.get("rejected_links") or "[]"),
    }


@router.post("")
async def ingest_text(body: IngestRequest):
    driver = get_driver()
    async with driver.session() as session:
        try:
            return await extract_from_text(session, body.text)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        return _batch_response(batch)


@router.get("/{batch_id}/entities/suggest")
async def suggest_entity(
    batch_id: str,
    label: str = Query(...),
    entity_class: str = Query(...),
    aliases: str = Query(""),
):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        batch_entities = json.loads(batch["entities"])
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
        try:
            match = await find_synonym_match(
                session, label, entity_class, alias_list, batch_entities
            )
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if match is None:
            return {"match": None, "reason": None}
        return {
            "match": {
                "entity_id": match["entity_id"],
                "label": match["label"],
                "aliases": match.get("aliases", []),
                "entity_class": match.get("entity_class", entity_class),
                "entity_subclass": match.get("entity_subclass", ""),
            },
            "reason": match.get("reason", ""),
        }


@router.post("/{batch_id}/entities")
async def add_batch_entity(batch_id: str, entity: EntityCreate):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        batch_entities = json.loads(batch["entities"])
        if any(e["entity_id"] == entity.entity_id for e in batch_entities):
            raise HTTPException(
                status_code=409,
                detail=f"entity_id '{entity.entity_id}' already used in this batch",
            )
        existing = await session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity.entity_id
        )
        if await existing.single() is not None:
            raise HTTPException(
                status_code=409, detail=f"entity_id '{entity.entity_id}' already exists"
            )

        try:
            await validate_entity(session, entity)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        batch_entities.append(entity.model_dump(mode="json"))
        new_entities_json = json.dumps(batch_entities)
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.entities = $entities",
            id=batch_id,
            entities=new_entities_json,
        )
        batch["entities"] = new_entities_json
        return _batch_response(batch)


@router.post("/{batch_id}/commit")
async def commit_batch(batch_id: str):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        entities = json.loads(batch["entities"])
        links = json.loads(batch["links"])

        for e in entities:
            entity = EntityCreate(**e)
            await session.run(
                "MERGE (n:Entity {entity_id: $id}) SET n += $props",
                id=entity.entity_id,
                props={**entity.model_dump(mode="json"), "attrs": json.dumps(entity.attrs)},
            )
        for l in links:
            link = LinkCreate(**l)
            await session.run(
                """
                MATCH (s:Entity {entity_id: $source_id}), (t:Entity {entity_id: $target_id})
                MERGE (s)-[r:LINK {link_id: $link_id}]->(t)
                SET r += $props
                """,
                source_id=link.source_entity,
                target_id=link.target_entity,
                link_id=link.link_id,
                props={**link.model_dump(mode="json"), "attrs": json.dumps(link.attrs)},
            )

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

Note this also refactors the existing `get_batch` and `commit_batch` to use the new `_get_batch`/`_require_proposed`/`_batch_response` helpers — same behavior, less duplication. `add_batch_link` (Task 3) isn't here yet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: PASS — all pre-existing tests (`test_get_batch_returns_entities_links_and_rejected`, `test_get_batch_404s_when_missing`, `test_commit_batch_also_flips_linked_document_to_committed`, `test_commit_batch_without_document_id_does_not_touch_document`) plus all new ones in this task.

Also run the full backend suite to confirm nothing else regressed: `npm run test:backend`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ingest.py backend/tests/test_ingest_api.py
git commit -m "feat(backend): add manual entity creation + synonym suggest to batch review"
```

---

## Task 3: `POST .../links`

**Files:**
- Modify: `backend/app/api/ingest.py`
- Test: `backend/tests/test_ingest_api.py`

**Interfaces:**
- Consumes: Task 2's `_get_batch`, `_require_proposed`, `_batch_response`.
- Produces: route `POST /ingest/{batch_id}/links`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ingest_api.py`:

```python
@pytest.mark.asyncio
async def test_add_batch_link_happy_path_referencing_batch_entity(monkeypatch):
    from app.api import ingest as ingest_module

    source = {
        "entity_id": "P-1",
        "entity_class": "PERSON",
        "entity_subclass": "PERSON.MILITARY_PERSONNEL",
        "label": "Ivan",
        "aliases": [],
        "status": "active",
        "confidence": "B2",
        "source_ref": "manual",
        "first_observed": None,
        "last_observed": None,
        "attrs": {},
    }
    target = {**source, "entity_id": "O-1", "entity_class": "ORGANIZATION"}
    session = _FakeSession(batch=_make_batch(entities=json.dumps([source, target])))
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-1",
                "target_entity": "O-1",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["links"]) == 1
    assert body["links"][0]["link_id"] == "L-1"


@pytest.mark.asyncio
async def test_add_batch_link_happy_path_referencing_committed_entity(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(
        batch=_make_batch(),
        committed_entities={
            "P-1": {"entity_id": "P-1", "entity_class": "PERSON"},
            "O-1": {"entity_id": "O-1", "entity_class": "ORGANIZATION"},
        },
    )
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-1",
                "target_entity": "O-1",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 200
    assert len(resp.json()["links"]) == 1


@pytest.mark.asyncio
async def test_add_batch_link_422s_on_unknown_endpoint(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch())
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-unknown",
                "target_entity": "O-unknown",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 422
    assert "neither in this batch nor an existing committed entity" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_batch_link_422s_on_domain_range_mismatch(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(
        batch=_make_batch(),
        committed_entities={
            "O-1": {"entity_id": "O-1", "entity_class": "ORGANIZATION"},
            "O-2": {"entity_id": "O-2", "entity_class": "ORGANIZATION"},
        },
    )
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # member_of requires domain=Person, but source is an Organization
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "O-1",
                "target_entity": "O-2",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 422
    assert "requires domain" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_batch_link_409s_when_batch_not_proposed(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(batch=_make_batch(status="committed"))
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-1",
                "target_entity": "O-1",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_batch_link_409s_on_id_collision_with_committed_link(monkeypatch):
    from app.api import ingest as ingest_module

    session = _FakeSession(
        batch=_make_batch(),
        committed_entities={
            "P-1": {"entity_id": "P-1", "entity_class": "PERSON"},
            "O-1": {"entity_id": "O-1", "entity_class": "ORGANIZATION"},
        },
        committed_links={"L-1": {"link_id": "L-1"}},
    )
    monkeypatch.setattr(ingest_module, "get_driver", lambda: _FakeDriver(session))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/ingest/B-1/links",
            json={
                "link_id": "L-1",
                "link_type": "member_of",
                "source_entity": "P-1",
                "target_entity": "O-1",
                "direction": "directed",
                "assertion_status": "reported",
                "confidence": "B2",
                "source_ref": "manual",
                "attrs": {},
            },
        )

    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: FAIL — the six new tests fail (route doesn't exist / 404).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/api/ingest.py`, change the import line:

```python
from app.ontology.validate import ValidationError, validate_entity
```
to:
```python
from app.ontology.validate import ValidationError, validate_entity, validate_link
```

Then insert this route between `add_batch_entity` and `commit_batch`:

```python
@router.post("/{batch_id}/links")
async def add_batch_link(batch_id: str, link: LinkCreate):
    driver = get_driver()
    async with driver.session() as session:
        batch = await _get_batch(session, batch_id)
        _require_proposed(batch)

        batch_links = json.loads(batch["links"])
        if any(l["link_id"] == link.link_id for l in batch_links):
            raise HTTPException(
                status_code=409, detail=f"link_id '{link.link_id}' already used in this batch"
            )
        existing_link = await session.run(
            "MATCH ()-[r:LINK {link_id: $id}]->() RETURN r LIMIT 1", id=link.link_id
        )
        if await existing_link.single() is not None:
            raise HTTPException(
                status_code=409, detail=f"link_id '{link.link_id}' already exists"
            )

        batch_entities = json.loads(batch["entities"])
        class_by_id = {e["entity_id"]: e["entity_class"] for e in batch_entities}
        for endpoint_id in (link.source_entity, link.target_entity):
            if endpoint_id in class_by_id:
                continue
            result = await session.run(
                "MATCH (e:Entity {entity_id: $id}) RETURN e", id=endpoint_id
            )
            record = await result.single()
            if record is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"link references entity '{endpoint_id}', which is neither in this "
                        "batch nor an existing committed entity"
                    ),
                )
            class_by_id[endpoint_id] = dict(record["e"])["entity_class"]

        try:
            await validate_link(
                session, link, class_by_id[link.source_entity], class_by_id[link.target_entity]
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        batch_links.append(link.model_dump(mode="json"))
        new_links_json = json.dumps(batch_links)
        await session.run(
            "MATCH (b:IngestBatch {batch_id: $id}) SET b.links = $links",
            id=batch_id,
            links=new_links_json,
        )
        batch["links"] = new_links_json
        return _batch_response(batch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && venv/bin/pytest tests/test_ingest_api.py -v`
Expected: PASS — all tests in the file.

Run the full backend suite: `npm run test:backend`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ingest.py backend/tests/test_ingest_api.py
git commit -m "feat(backend): add manual link creation to batch review"
```

---

## Task 4: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `SynonymMatch` and `SuggestEntityResponse` types; `api.suggestBatchEntity`, `api.addBatchEntity`, `api.addBatchLink` — consumed by Tasks 6 and 8.

- [ ] **Step 1: Add the new types**

In `frontend/src/lib/types.ts`, add after the `IngestBatch` interface:

```typescript
export interface SynonymMatch {
  entity_id: string
  label: string
  aliases: string[]
  entity_class: string
  entity_subclass: string
}

export interface SuggestEntityResponse {
  match: SynonymMatch | null
  reason: string | null
}
```

- [ ] **Step 2: Add the API client functions**

In `frontend/src/lib/api.ts`, add `SuggestEntityResponse` to the type import list (alphabetical, matching the existing order):

```typescript
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
  SuggestEntityResponse,
} from './types'
```

Then add these three methods to the `api` object, right after `getBatch`:

```typescript
  suggestBatchEntity: (batchId: string, label: string, entityClass: string, aliases: string[]) => {
    const params = new URLSearchParams({ label, entity_class: entityClass })
    if (aliases.length) params.set('aliases', aliases.join(','))
    return request<SuggestEntityResponse>(
      `/ingest/${encodeURIComponent(batchId)}/entities/suggest?${params}`,
    )
  },
  addBatchEntity: (batchId: string, entity: EntityCreateInput) =>
    request<IngestBatch>(`/ingest/${encodeURIComponent(batchId)}/entities`, {
      method: 'POST',
      body: JSON.stringify(entity),
    }),
  addBatchLink: (batchId: string, link: LinkCreateInput) =>
    request<IngestBatch>(`/ingest/${encodeURIComponent(batchId)}/links`, {
      method: 'POST',
      body: JSON.stringify(link),
    }),
```

- [ ] **Step 3: Verify it compiles**

Run: `npm run test:frontend`
Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): add batch-scoped entity/link creation API client"
```

---

## Task 5: Refactor `EntityForm`/`LinkForm` to accept `onSubmit`

**Files:**
- Modify: `frontend/src/components/layout/AddResourceDialog.tsx`

**Interfaces:**
- Produces: exported `EntityForm({ onSubmit: (entity: EntityCreateInput) => Promise<string>, onCreated: (id: string) => void })` and `LinkForm({ onSubmit: (link: LinkCreateInput) => Promise<string>, onCreated: (id: string) => void })` — `onSubmit` resolves to the id `onCreated` receives. Consumed by Task 6 (adds props to `EntityForm`), Task 7 (adds a prop to `LinkForm`), and Task 8 (`AddToBatchDialog` imports both).

This is a pure refactor — `AddResourceDialog`'s behavior must be identical before and after. No new automated test exists for this component today (see Global Constraints), so this task is verified by type-check plus a manual smoke test of the *existing* flow.

- [ ] **Step 1: Add the new types to the existing type import, then change `EntityForm`'s signature and submit handler**

In `frontend/src/components/layout/AddResourceDialog.tsx`, find:

```typescript
import type { ClassDef, LinkDef } from '../../lib/types'
```

Replace with:

```typescript
import type { ClassDef, EntityCreateInput, LinkCreateInput, LinkDef } from '../../lib/types'
```

Find:

```typescript
function EntityForm({ onCreated }: { onCreated: (id: string) => void }) {
```

Replace with:

```typescript
export function EntityForm({
  onSubmit,
  onCreated,
}: {
  onSubmit: (entity: EntityCreateInput) => Promise<string>
  onCreated: (id: string) => void
}) {
```

Find:

```typescript
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const created = await api.createEntity({
        entity_id: entityId.trim(),
        entity_class: entityClass,
        entity_subclass: entitySubclass,
        label: label.trim(),
        aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
        status: status as 'active' | 'inactive' | 'destroyed' | 'unknown',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(created.entity_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }
```

Replace with:

```typescript
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const id = await onSubmit({
        entity_id: entityId.trim(),
        entity_class: entityClass,
        entity_subclass: entitySubclass,
        label: label.trim(),
        aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
        status: status as 'active' | 'inactive' | 'destroyed' | 'unknown',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }
```

- [ ] **Step 2: Same for `LinkForm`**

Find:

```typescript
function LinkForm({ onCreated }: { onCreated: (id: string) => void }) {
```

Replace with:

```typescript
export function LinkForm({
  onSubmit,
  onCreated,
}: {
  onSubmit: (link: LinkCreateInput) => Promise<string>
  onCreated: (id: string) => void
}) {
```

Find:

```typescript
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const created = await api.createLink({
        link_id: linkId.trim(),
        link_type: linkType,
        source_entity: sourceEntity.trim(),
        target_entity: targetEntity.trim(),
        direction: direction as 'directed' | 'symmetric',
        assertion_status: assertionStatus as 'reported' | 'assessed' | 'confirmed' | 'disputed',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(created.source_entity)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }
```

Replace with:

```typescript
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const id = await onSubmit({
        link_id: linkId.trim(),
        link_type: linkType,
        source_entity: sourceEntity.trim(),
        target_entity: targetEntity.trim(),
        direction: direction as 'directed' | 'symmetric',
        assertion_status: assertionStatus as 'reported' | 'assessed' | 'confirmed' | 'disputed',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }
```

- [ ] **Step 3: Update `AddResourceDialog`'s call sites**

Find:

```tsx
            <Tabs.Content value="entity">
              <EntityForm onCreated={handleCreated} />
            </Tabs.Content>
            <Tabs.Content value="link">
              <LinkForm onCreated={handleCreated} />
            </Tabs.Content>
```

Replace with:

```tsx
            <Tabs.Content value="entity">
              <EntityForm
                onSubmit={(entity) => api.createEntity(entity).then((created) => created.entity_id)}
                onCreated={handleCreated}
              />
            </Tabs.Content>
            <Tabs.Content value="link">
              <LinkForm
                onSubmit={(link) => api.createLink(link).then((created) => created.source_entity)}
                onCreated={handleCreated}
              />
            </Tabs.Content>
```

- [ ] **Step 4: Verify it compiles**

Run: `npm run test:frontend`
Expected: no TypeScript errors.

- [ ] **Step 5: Manual smoke test (regression check on existing behavior)**

Run: `npm run dev`. Open the app, trigger "Add to graph" (`AddResourceDialog`) from wherever it's currently opened in the app (check `grep -rn "AddResourceDialog" frontend/src` for its trigger point if not obvious from the canvas UI), create one entity and one link via the two tabs exactly as before, and confirm both still land in the graph (canvas jumps to the new entity, as `onCreated` already did pre-refactor).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/AddResourceDialog.tsx
git commit -m "refactor(frontend): let EntityForm/LinkForm submit via a caller-supplied onSubmit"
```

---

## Task 6: Synonym-check banner in `EntityForm`

**Files:**
- Modify: `frontend/src/components/layout/AddResourceDialog.tsx`

**Interfaces:**
- Consumes: Task 4's `SynonymMatch` type.
- Produces: `EntityForm`'s new optional props `onCheckSynonym?: (label: string, entityClass: string, aliases: string[]) => Promise<{ match: SynonymMatch; reason: string } | null>` and `onUseExisting?: (entityId: string) => void`. Consumed by Task 8.

Neither prop is passed by `AddResourceDialog` (still calling `<EntityForm onSubmit=... onCreated=... />` unchanged from Task 5), so this task's new code path has no live caller yet — it's exercised end-to-end once Task 8 wires `AddToBatchDialog`. This task's own verification is type-check only.

- [ ] **Step 1: Add `SynonymMatch` to the type import, then add the props/state/synonym-check branch to `EntityForm`**

In `frontend/src/components/layout/AddResourceDialog.tsx`, find:

```typescript
import type { ClassDef, EntityCreateInput, LinkCreateInput, LinkDef } from '../../lib/types'
```

Replace with:

```typescript
import type { ClassDef, EntityCreateInput, LinkCreateInput, LinkDef, SynonymMatch } from '../../lib/types'
```

Find the `EntityForm` signature (as left by Task 5):

```typescript
export function EntityForm({
  onSubmit,
  onCreated,
}: {
  onSubmit: (entity: EntityCreateInput) => Promise<string>
  onCreated: (id: string) => void
}) {
```

Replace with:

```typescript
export function EntityForm({
  onSubmit,
  onCreated,
  onCheckSynonym,
  onUseExisting,
}: {
  onSubmit: (entity: EntityCreateInput) => Promise<string>
  onCreated: (id: string) => void
  onCheckSynonym?: (
    label: string,
    entityClass: string,
    aliases: string[],
  ) => Promise<{ match: SynonymMatch; reason: string } | null>
  onUseExisting?: (entityId: string) => void
}) {
```

Find the local state block (right after the signature):

```typescript
  const [attrRows, setAttrRows] = useState<{ key: string; value: string }[]>([])
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
```

Replace with:

```typescript
  const [attrRows, setAttrRows] = useState<{ key: string; value: string }[]>([])
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [checkNote, setCheckNote] = useState('')
  const [pendingMatch, setPendingMatch] = useState<{
    match: SynonymMatch
    reason: string
    payload: EntityCreateInput
  } | null>(null)
```

Find `handleSubmit` (as left by Task 5) and replace it with:

```typescript
  async function doCreate(payload: EntityCreateInput) {
    setSubmitting(true)
    try {
      const id = await onSubmit(payload)
      onCreated(id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setCheckNote('')
    const payload: EntityCreateInput = {
      entity_id: entityId.trim(),
      entity_class: entityClass,
      entity_subclass: entitySubclass,
      label: label.trim(),
      aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
      status: status as 'active' | 'inactive' | 'destroyed' | 'unknown',
      confidence: confidence.trim(),
      source_ref: sourceRef.trim(),
      attrs: attrsToRecord(attrRows),
    }

    if (onCheckSynonym) {
      setSubmitting(true)
      try {
        const result = await onCheckSynonym(payload.label, payload.entity_class, payload.aliases)
        setSubmitting(false)
        if (result) {
          setPendingMatch({ ...result, payload })
          return
        }
      } catch {
        setCheckNote("Couldn't check for duplicates — continuing without it.")
        setSubmitting(false)
      }
    }

    await doCreate(payload)
  }

  function handleUseExisting() {
    if (pendingMatch) onUseExisting?.(pendingMatch.match.entity_id)
    setPendingMatch(null)
  }

  function handleCreateAnyway() {
    const payload = pendingMatch?.payload
    setPendingMatch(null)
    if (payload) void doCreate(payload)
  }
```

- [ ] **Step 2: Render the banner instead of the submit button when a match is pending**

Find the submit button at the end of `EntityForm`'s JSX:

```tsx
      <Button
        type="submit"
        variant="primary"
        disabled={submitting || !entitySubclass || !label.trim() || !confidence.trim() || !sourceRef.trim()}
      >
        {submitting ? 'Creating…' : 'Create entity'}
      </Button>
    </form>
  )
}
```

Replace with:

```tsx
      {checkNote && <p className="text-xs text-[var(--color-text-muted)]">{checkNote}</p>}

      {pendingMatch ? (
        <div className="rounded-md border border-[var(--color-border)] p-3 text-sm">
          <p className="text-[var(--color-text-primary)]">
            Looks like an existing entity: <strong>{pendingMatch.match.label}</strong> (
            {pendingMatch.match.entity_subclass}). {pendingMatch.reason}
          </p>
          <div className="mt-2 flex gap-2">
            <Button type="button" variant="primary" onClick={handleUseExisting}>
              Use existing
            </Button>
            <Button type="button" onClick={handleCreateAnyway}>
              Create new anyway
            </Button>
          </div>
        </div>
      ) : (
        <Button
          type="submit"
          variant="primary"
          disabled={submitting || !entitySubclass || !label.trim() || !confidence.trim() || !sourceRef.trim()}
        >
          {submitting ? 'Creating…' : 'Create entity'}
        </Button>
      )}
    </form>
  )
}
```

- [ ] **Step 3: Verify it compiles**

Run: `npm run test:frontend`
Expected: no TypeScript errors. (`AddResourceDialog`'s existing `<EntityForm onSubmit=... onCreated=... />` call doesn't pass the two new optional props, which is valid since they're optional.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/AddResourceDialog.tsx
git commit -m "feat(frontend): add synonym-confirmation banner to EntityForm"
```

---

## Task 7: `EntityPicker` + `LinkForm` source/target autocomplete

**Files:**
- Create: `frontend/src/components/layout/EntityPicker.tsx`
- Modify: `frontend/src/components/layout/AddResourceDialog.tsx`

**Interfaces:**
- Produces: `EntityPicker({ value, onChange, localEntities, placeholder?, 'aria-label'? })`; `LinkForm`'s new optional prop `localEntities?: Entity[]` (when provided, source/target become `EntityPicker`s instead of plain text inputs — `AddResourceDialog`'s own `<LinkForm>` call, unchanged, doesn't pass it, so its behavior is identical to before). Consumed by Task 8.

- [ ] **Step 1: Create `EntityPicker`**

Create `frontend/src/components/layout/EntityPicker.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { Entity } from '../../lib/types'

interface EntityPickerProps {
  value: string
  onChange: (entityId: string) => void
  /** Entities already known locally (e.g. this batch's own staged
   * entities) — matched alongside the backend substring search, so a
   * freshly-added-but-not-yet-committed entity is pickable immediately. */
  localEntities: Entity[]
  placeholder?: string
  'aria-label'?: string
}

/** Free-text entity_id input with a live suggestions dropdown, combining
 * locally-known entities and the backend's /entities/search (existing
 * committed entities) — the source/target picker for manually-added
 * batch links, which may point at either. */
export function EntityPicker({ value, onChange, localEntities, placeholder, ...aria }: EntityPickerProps) {
  const [query, setQuery] = useState(value)
  const [remoteResults, setRemoteResults] = useState<Entity[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setQuery(value)
  }, [value])

  useEffect(() => {
    if (!query.trim()) {
      setRemoteResults([])
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .searchEntities(query.trim())
        .then((results) => {
          if (!cancelled) setRemoteResults(results)
        })
        .catch(() => {
          if (!cancelled) setRemoteResults([])
        })
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  const needle = query.trim().toLowerCase()
  const localMatches = localEntities.filter(
    (e) => e.entity_id.toLowerCase().includes(needle) || e.label.toLowerCase().includes(needle),
  )
  const seen = new Set(localMatches.map((e) => e.entity_id))
  const suggestions = [...localMatches, ...remoteResults.filter((e) => !seen.has(e.entity_id))]

  function pick(entity: Entity) {
    onChange(entity.entity_id)
    setQuery(entity.entity_id)
    setOpen(false)
  }

  return (
    <div className="relative flex flex-col gap-1.5">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        aria-label={aria['aria-label']}
        className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
      />
      {open && needle && suggestions.length > 0 && (
        <ul className="absolute top-full z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] shadow-lg">
          {suggestions.map((entity) => (
            <li key={entity.entity_id}>
              <button
                type="button"
                onMouseDown={() => pick(entity)}
                className="flex w-full flex-col items-start px-2.5 py-1.5 text-start hover:bg-[var(--color-surface-hover)]"
              >
                <span className="text-sm text-[var(--color-text-primary)]">{entity.label}</span>
                <span className="font-mono text-xs text-[var(--color-text-muted)]">{entity.entity_id}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Wire it into `LinkForm`, gated by a new optional prop**

In `frontend/src/components/layout/AddResourceDialog.tsx`, add the component import at the top (alongside the other relative imports):

```typescript
import { EntityPicker } from './EntityPicker'
```

Find:

```typescript
import type { ClassDef, EntityCreateInput, LinkCreateInput, LinkDef, SynonymMatch } from '../../lib/types'
```

Replace with:

```typescript
import type { ClassDef, Entity, EntityCreateInput, LinkCreateInput, LinkDef, SynonymMatch } from '../../lib/types'
```

Find the `LinkForm` signature (as left by Task 5):

```typescript
export function LinkForm({
  onSubmit,
  onCreated,
}: {
  onSubmit: (link: LinkCreateInput) => Promise<string>
  onCreated: (id: string) => void
}) {
```

Replace with:

```typescript
export function LinkForm({
  onSubmit,
  onCreated,
  localEntities,
}: {
  onSubmit: (link: LinkCreateInput) => Promise<string>
  onCreated: (id: string) => void
  /** When provided (batch-review context), source/target become a
   * searchable EntityPicker. Undefined preserves AddResourceDialog's
   * original plain-text-input behavior. */
  localEntities?: Entity[]
}) {
```

Find the source/target `TextField`s:

```tsx
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Source entity ID"
          value={sourceEntity}
          onChange={(e) => setSourceEntity(e.target.value)}
          placeholder="e.g. P-1042"
          required
        />
        <TextField
          label="Target entity ID"
          value={targetEntity}
          onChange={(e) => setTargetEntity(e.target.value)}
          placeholder="e.g. O-233"
          required
        />
      </div>
```

Replace with:

```tsx
      <div className="grid grid-cols-2 gap-3">
        {localEntities !== undefined ? (
          <>
            <div className="flex flex-col gap-1.5">
              {labelFor('Source entity ID')}
              <EntityPicker
                value={sourceEntity}
                onChange={setSourceEntity}
                localEntities={localEntities}
                placeholder="e.g. P-1042"
                aria-label="Source entity ID"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              {labelFor('Target entity ID')}
              <EntityPicker
                value={targetEntity}
                onChange={setTargetEntity}
                localEntities={localEntities}
                placeholder="e.g. O-233"
                aria-label="Target entity ID"
              />
            </div>
          </>
        ) : (
          <>
            <TextField
              label="Source entity ID"
              value={sourceEntity}
              onChange={(e) => setSourceEntity(e.target.value)}
              placeholder="e.g. P-1042"
              required
            />
            <TextField
              label="Target entity ID"
              value={targetEntity}
              onChange={(e) => setTargetEntity(e.target.value)}
              placeholder="e.g. O-233"
              required
            />
          </>
        )}
      </div>
```

- [ ] **Step 3: Verify it compiles**

Run: `npm run test:frontend`
Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/EntityPicker.tsx frontend/src/components/layout/AddResourceDialog.tsx
git commit -m "feat(frontend): add EntityPicker autocomplete, opt-in for LinkForm"
```

---

## Task 8: Wire `BatchReviewPanel` — `AddToBatchDialog`, buttons, `onBatchUpdated`

**Files:**
- Create: `frontend/src/components/layout/AddToBatchDialog.tsx`
- Modify: `frontend/src/components/layout/BatchReviewPanel.tsx`
- Modify: `frontend/src/components/layout/IngestDialog.tsx`
- Modify: `frontend/src/components/knowledge/DocumentReviewDialog.tsx`

**Interfaces:**
- Consumes: Task 4's `api.suggestBatchEntity`/`addBatchEntity`/`addBatchLink`; Task 5's exported `EntityForm`/`LinkForm`; Task 6's `onCheckSynonym`/`onUseExisting`; Task 7's `localEntities`.
- Produces: the complete end-to-end feature.

- [ ] **Step 1: Create `AddToBatchDialog`**

Create `frontend/src/components/layout/AddToBatchDialog.tsx`:

```tsx
import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { X } from 'lucide-react'
import { api } from '../../lib/api'
import type { EntityCreateInput, IngestBatch, LinkCreateInput } from '../../lib/types'
import { EntityForm, LinkForm } from './AddResourceDialog'

interface AddToBatchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  batch: IngestBatch
  defaultTab: 'entity' | 'link'
  onBatchUpdated: (batch: IngestBatch) => void
}

/** "+Add entity"/"+Add link" for a proposed IngestBatch under review —
 * reuses AddResourceDialog's forms, but targets the batch-scoped
 * POST /ingest/{id}/entities|links endpoints (staged, not committed) and
 * wires the entity form's synonym-suggestion step. */
export function AddToBatchDialog({ open, onOpenChange, batch, defaultTab, onBatchUpdated }: AddToBatchDialogProps) {
  function handleDone() {
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Add to batch
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

          <Tabs.Root defaultValue={defaultTab}>
            <Tabs.List className="mb-4 flex gap-1 border-b border-[var(--color-border)]" aria-label="Resource type">
              <Tabs.Trigger
                value="entity"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Entity
              </Tabs.Trigger>
              <Tabs.Trigger
                value="link"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Link
              </Tabs.Trigger>
            </Tabs.List>
            <Tabs.Content value="entity">
              <EntityForm
                onSubmit={(entity: EntityCreateInput) =>
                  api.addBatchEntity(batch.batch_id, entity).then((updated) => {
                    onBatchUpdated(updated)
                    return entity.entity_id
                  })
                }
                onCheckSynonym={(label, entityClass, aliases) =>
                  api
                    .suggestBatchEntity(batch.batch_id, label, entityClass, aliases)
                    .then((r) => (r.match ? { match: r.match, reason: r.reason ?? '' } : null))
                }
                onUseExisting={handleDone}
                onCreated={handleDone}
              />
            </Tabs.Content>
            <Tabs.Content value="link">
              <LinkForm
                onSubmit={(link: LinkCreateInput) =>
                  api.addBatchLink(batch.batch_id, link).then((updated) => {
                    onBatchUpdated(updated)
                    return link.source_entity
                  })
                }
                onCreated={handleDone}
                localEntities={batch.entities}
              />
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
```

- [ ] **Step 2: Wire `BatchReviewPanel`**

Read the current `frontend/src/components/layout/BatchReviewPanel.tsx` in full before editing (it's short — 122 lines).

Replace its entire contents with:

```tsx
import { useState } from 'react'
import type { ReactNode } from 'react'
import type { IngestBatch } from '../../lib/types'
import { AddToBatchDialog } from './AddToBatchDialog'
import { Button } from '../ui/Button'
import { ConfidenceChip, ProposedChip } from '../ui/Chip'

interface BatchReviewPanelProps {
  batch: IngestBatch
  onCommit: () => void
  committing: boolean
  commitError: string
  /** Called with the server's updated batch after a manual entity/link add
   * so the caller's batch state (and this panel's tables) stay in sync. */
  onBatchUpdated: (batch: IngestBatch) => void
  /** Extra button(s) rendered before Commit, e.g. IngestDialog's "Start
   * over" — DocumentReviewDialog has no equivalent and omits this. */
  extraActions?: ReactNode
}

/** Entities/links/rejected review table + commit button — shared between
 * IngestDialog (paste-text flow, batch held in local state right after
 * extraction) and DocumentReviewDialog (upload flow, batch fetched by id
 * from a background-produced IngestBatch). This component only renders
 * and reports commit/add intent via callbacks; it never fetches or
 * resets on its own. */
export function BatchReviewPanel({
  batch,
  onCommit,
  committing,
  commitError,
  onBatchUpdated,
  extraActions,
}: BatchReviewPanelProps) {
  const [addTab, setAddTab] = useState<'entity' | 'link' | null>(null)

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Proposed from this text — review before committing to the graph.
      </p>

      <ResultTable
        title={`Entities (${batch.entities.length})`}
        empty="No entities extracted."
        action={<Button onClick={() => setAddTab('entity')}>+ Add entity</Button>}
      >
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

      <ResultTable
        title={`Links (${batch.links.length})`}
        empty="No links extracted."
        action={<Button onClick={() => setAddTab('link')}>+ Add link</Button>}
      >
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

      {addTab && (
        <AddToBatchDialog
          open={addTab !== null}
          onOpenChange={(open) => !open && setAddTab(null)}
          batch={batch}
          defaultTab={addTab}
          onBatchUpdated={onBatchUpdated}
        />
      )}
    </div>
  )
}

function ResultTable({
  title,
  empty,
  action,
  children,
}: {
  title: string
  empty: string
  action?: ReactNode
  children: ReactNode
}) {
  const hasRows = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          {title}
        </h3>
        {action}
      </div>
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

- [ ] **Step 3: Pass `onBatchUpdated` from `IngestDialog`**

In `frontend/src/components/layout/IngestDialog.tsx`, find:

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

Replace with:

```tsx
          {(phase === 'review' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
              onBatchUpdated={setBatch}
              extraActions={
                <Button onClick={reset} disabled={phase === 'committing'}>
                  Start over
                </Button>
              }
            />
          )}
```

- [ ] **Step 4: Pass `onBatchUpdated` from `DocumentReviewDialog`**

In `frontend/src/components/knowledge/DocumentReviewDialog.tsx`, find:

```tsx
          {(phase === 'ready' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
            />
          )}
```

Replace with:

```tsx
          {(phase === 'ready' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
              onBatchUpdated={setBatch}
            />
          )}
```

- [ ] **Step 5: Verify it compiles**

Run: `npm run test:frontend`
Expected: no TypeScript errors.

- [ ] **Step 6: Full manual verification**

1. Ensure the stack is up: `docker compose up -d` (Neo4j), then `npm run dev` (backend :8000, frontend :5173). Confirm `.env`/`.env.local` (or frontend env) has a working `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` — the synonym check needs a real LLM call.
2. Open the app, use "Ingest scenario text" with a short paste that plausibly extracts **nothing** in-domain (e.g. a sentence with no named people/orgs/vehicles) to reach a batch with 0 entities/0 links. Confirm the Commit button is disabled and "+ Add entity"/"+ Add link" are visible on their respective table headers.
3. Click **+ Add entity**, fill in a new entity (pick any leaf `entity_subclass`), submit. Confirm no synonym banner appears (nothing to match against yet in a clean graph) and the entity appears in the Entities table; Commit un-disables.
4. Click **+ Add entity** again, enter a label that's a deliberate synonym of the one just created (e.g. if you created "Car" under a VEHICLE leaf class, now try "Automobile" under the same root class). Confirm the synonym banner appears with a reason, and both **Use existing** (closes the dialog, no new row added) and, on a fresh attempt, **Create new anyway** (adds a second row) work as expected.
5. Click **+ Add link**, confirm the source/target fields show the autocomplete dropdown (typing part of the entity's label or id surfaces it), pick a valid link type connecting the two entities' classes per the ontology, submit, and confirm it appears in the Links table.
6. Commit the batch and confirm it lands in the graph (existing commit flow, unchanged).
7. Repeat steps 2–6 via the Knowledge base upload flow (`DocumentReviewDialog`) to confirm the same behavior there.
8. Re-open `AddResourceDialog` (the pre-existing direct-to-graph "Add to graph" flow) and confirm it still behaves exactly as before — no synonym banner, plain text source/target fields (Task 5/7's opt-in guards working correctly).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/layout/AddToBatchDialog.tsx frontend/src/components/layout/BatchReviewPanel.tsx frontend/src/components/layout/IngestDialog.tsx frontend/src/components/knowledge/DocumentReviewDialog.tsx
git commit -m "feat(frontend): wire manual entity/link creation into batch review"
```
