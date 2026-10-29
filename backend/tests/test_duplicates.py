"""Duplicate detection + the reviewer's decisions (pure logic and API)."""
import json

import httpx
import pytest

from app.main import create_app
from app.services.duplicates import (
    DuplicateDecisionError,
    apply_decisions,
    find_duplicates,
    match_reason,
    normalize_label,
)


def ent(entity_id, label, cls="PERSON", aliases=None):
    return {
        "entity_id": entity_id,
        "label": label,
        "aliases": aliases or [],
        "entity_class": cls,
        "entity_subclass": f"{cls}.X",
    }


def test_normalize_label_folds_case_accents_punctuation():
    assert normalize_label("  Ivan  PETROV, Jr. ") == "ivan petrov jr"
    assert normalize_label("Zoë Müller") == "zoe muller"
    assert normalize_label(None) == ""


@pytest.mark.parametrize(
    "a,b,reason",
    [
        (ent("1", "Ivan Petrov"), ent("2", "ivan  petrov."), "same_label"),
        (ent("1", "Ivan Petrov", aliases=["Vanya"]), ent("2", "Vanya"), "alias"),
        (ent("1", "Petrov Ivan"), ent("2", "Ivan Petrov"), "reordered"),
        (ent("1", "Kursk Airbase"), ent("2", "Kursk Airbse"), "similar"),
    ],
)
def test_match_reason(a, b, reason):
    assert match_reason(a, b)[0] == reason


def test_match_reason_ignores_different_things_and_classes():
    assert match_reason(ent("1", "Ivan Petrov"), ent("2", "Anna Petrova")) is None
    assert match_reason(ent("1", "Mercury", "PERSON"), ent("2", "Mercury", "LOCATION")) is None
    assert match_reason(ent("1", "T-72"), ent("2", "T-80")) is None  # too short to fuzzy-match


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r

        return gen()


class _GraphSession:
    def __init__(self, graph):
        self.graph = graph

    async def run(self, query, **params):
        if "RETURN e.entity_id AS id" in query:
            return _Result([{"id": e["entity_id"]} for e in self.graph if e["entity_id"] in params["ids"]])
        return _Result(self.graph)


@pytest.mark.asyncio
async def test_find_duplicates_flags_graph_same_id_and_within_batch_pairs_once():
    graph = [ent("P-temp1", "Someone Else"), ent("P-9", "Anna Petrova")]
    batch = [
        ent("P-temp1", "Brand New Person"),  # id collision: commit would overwrite P-temp1
        ent("P-2", "anna petrova"),  # same label as graph P-9
        ent("P-3", "Boris Orlov"),
        ent("P-4", "Orlov Boris"),  # reordered twin of P-3 (flagged once, on the later one)
        ent("P-5", "Unique Name Here"),
    ]
    found = await find_duplicates(_GraphSession(graph), batch, {})
    by_id = {d["entity_id"]: d for d in found}

    assert set(by_id) == {"P-temp1", "P-2", "P-4"}
    assert by_id["P-temp1"]["matches"][0]["reason"] == "same_id"
    assert by_id["P-temp1"]["matches"][0]["where"] == "graph"
    assert by_id["P-2"]["matches"][0]["entity_id"] == "P-9"
    assert by_id["P-4"]["matches"][0]["where"] == "batch"
    assert by_id["P-4"]["matches"][0]["entity_id"] == "P-3"


@pytest.mark.asyncio
async def test_find_duplicates_skips_kept_separate_and_empty_batch():
    graph = [ent("P-9", "Anna Petrova")]
    batch = [ent("P-2", "Anna Petrova")]
    assert await find_duplicates(_GraphSession(graph), [], {}) == []
    kept = await find_duplicates(_GraphSession(graph), batch, {"P-2": {"action": "keep_separate"}})
    assert kept == []
    # a "remove" decision never leaves the entity in the batch, so nothing to re-flag


def link(lid, src, tgt, t="member_of"):
    return {"link_id": lid, "link_type": t, "source_entity": src, "target_entity": tgt}


def unused(base, taken):
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def test_remove_drops_entity_and_repoints_links_without_losing_relationships():
    entities = [ent("P-2", "Anna Petrova"), ent("O-1", "Unit", "ORGANIZATION")]
    links = [
        link("L1", "P-2", "O-1"),  # re-pointed to P-9
        link("L2", "O-1", "P-2", "commands"),  # re-pointed
        link("L3", "P-2", "P-9"),  # would become a self-link -> dropped
        link("L4", "P-9", "O-1"),  # same triple as re-pointed L1 -> deduped
    ]
    new_e, new_l, decisions = apply_decisions(
        entities, links, {}, [{"entity_id": "P-2", "action": "remove", "target_id": "P-9"}],
        known_target_ids={"P-9"}, unused_id=unused,
    )
    assert [e["entity_id"] for e in new_e] == ["O-1"]
    assert {(l["link_id"], l["source_entity"], l["target_entity"]) for l in new_l} == {
        ("L1", "P-9", "O-1"),  # first of the two that collapse to the same triple is kept
        ("L2", "O-1", "P-9"),
    }
    assert decisions["P-2"] == {"action": "remove", "into": "P-9"}


def test_remove_into_batch_entity_and_validation_errors():
    entities = [ent("P-1", "A"), ent("P-2", "A")]
    new_e, _, _ = apply_decisions(
        entities, [], {}, [{"entity_id": "P-2", "action": "remove", "target_id": "P-1"}],
        known_target_ids=set(), unused_id=unused,
    )
    assert [e["entity_id"] for e in new_e] == ["P-1"]
    for bad in (
        {"entity_id": "P-2", "action": "remove"},  # no target
        {"entity_id": "P-2", "action": "remove", "target_id": "P-2"},  # itself
        {"entity_id": "P-2", "action": "remove", "target_id": "nope"},  # unknown target
        {"entity_id": "ghost", "action": "keep_separate"},  # not in batch
        {"entity_id": "P-2", "action": "explode"},
    ):
        with pytest.raises(DuplicateDecisionError):
            apply_decisions(entities, [], {}, [bad], known_target_ids=set(), unused_id=unused)


def test_keep_separate_renames_only_when_id_collides_with_graph():
    entities = [ent("P-temp1", "New Person"), ent("P-7", "Other")]
    links = [link("L1", "P-temp1", "P-7")]
    new_e, new_l, decisions = apply_decisions(
        entities, links, {}, [{"entity_id": "P-temp1", "action": "keep_separate"}],
        known_target_ids={"P-temp1", "P-temp1-2"}, unused_id=unused,
    )
    assert new_e[0]["entity_id"] == "P-temp1-3"
    assert new_l[0]["source_entity"] == "P-temp1-3"
    assert decisions == {"P-temp1-3": {"action": "keep_separate"}}

    new_e, new_l, decisions = apply_decisions(
        entities, links, {}, [{"entity_id": "P-7", "action": "keep_separate"}],
        known_target_ids=set(), unused_id=unused,
    )
    assert [e["entity_id"] for e in new_e] == ["P-temp1", "P-7"]
    assert decisions == {"P-7": {"action": "keep_separate"}}


# ---- API -------------------------------------------------------------------------


class _Single:
    def __init__(self, record):
        self._r = record

    async def single(self):
        return self._r

    def __aiter__(self):
        async def gen():
            if False:
                yield

        return gen()


class _ApiSession:
    def __init__(self, batch, graph):
        self.batch, self.graph, self.calls = batch, graph, []

    async def run(self, query, **params):
        self.calls.append((query, params))
        if "MATCH (b:IngestBatch {batch_id: $id}) RETURN b" in query:
            return _Single({"b": self.batch})
        if "SET b.entities = $entities, b.links = $links, b.duplicate_decisions" in query:
            self.batch.update(
                entities=params["entities"], links=params["links"], duplicate_decisions=params["decisions"]
            )
            return _Single(None)
        if "RETURN e.entity_id AS id" in query:
            return _Result([{"id": e["entity_id"]} for e in self.graph if e["entity_id"] in params["ids"]])
        if "MATCH (e:Entity)" in query and "LIMIT $limit" in query:
            return _Result(self.graph)
        return _Single(None)


class _Ctx:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


def _batch(entities, links=()):
    return {
        "batch_id": "B-1",
        "status": "proposed",
        "source_text": "t",
        "entities": json.dumps(entities),
        "links": json.dumps(list(links)),
        "rejected_entities": "[]",
        "rejected_links": "[]",
    }


async def _call(monkeypatch, session, method, path, **kw):
    from types import SimpleNamespace

    from app.api import ingest as ingest_module

    monkeypatch.setattr(ingest_module, "get_driver", lambda: SimpleNamespace(session=lambda: _Ctx(session)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        return await c.request(method, path, **kw)


@pytest.mark.asyncio
async def test_duplicates_endpoint_lists_then_resolve_clears_them(monkeypatch):
    graph = [ent("P-9", "Anna Petrova")]
    session = _ApiSession(_batch([ent("P-2", "Anna Petrova")], [link("L1", "P-2", "P-9")]), graph)

    res = await _call(monkeypatch, session, "GET", "/ingest/B-1/duplicates")
    assert res.status_code == 200
    assert [d["entity_id"] for d in res.json()["duplicates"]] == ["P-2"]

    res = await _call(
        monkeypatch, session, "POST", "/ingest/B-1/duplicates/resolve",
        json={"decisions": [{"entity_id": "P-2", "action": "remove", "target_id": "P-9"}]},
    )
    assert res.status_code == 200
    assert res.json()["entities"] == []
    assert res.json()["links"] == []  # its only link became a self-link
    assert json.loads(session.batch["duplicate_decisions"]) == {"P-2": {"action": "remove", "into": "P-9"}}


@pytest.mark.asyncio
async def test_resolve_rejects_bad_requests_and_non_proposed_batches(monkeypatch):
    session = _ApiSession(_batch([ent("P-2", "Anna")]), [])
    res = await _call(
        monkeypatch, session, "POST", "/ingest/B-1/duplicates/resolve",
        json={"decisions": [{"entity_id": "P-2", "action": "remove", "target_id": "ghost"}]},
    )
    assert res.status_code == 422

    session.batch["status"] = "committed"
    res = await _call(
        monkeypatch, session, "POST", "/ingest/B-1/duplicates/resolve",
        json={"decisions": [{"entity_id": "P-2", "action": "keep_separate"}]},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_commit_is_blocked_until_duplicates_are_decided(monkeypatch):
    graph = [ent("P-9", "Anna Petrova")]
    session = _ApiSession(_batch([ent("P-2", "Anna Petrova")]), graph)

    res = await _call(monkeypatch, session, "POST", "/ingest/B-1/commit")
    assert res.status_code == 409
    assert "possible duplicate" in res.json()["detail"]
    assert not any("MERGE (n:Entity" in q for q, _ in session.calls)  # nothing written

    session.batch["duplicate_decisions"] = json.dumps({"P-2": {"action": "keep_separate"}})
    session.batch["entities"] = json.dumps([{**ent("P-2", "Anna Petrova"), "confidence": "B2", "source_ref": "x", "status": "active"}])
    res = await _call(monkeypatch, session, "POST", "/ingest/B-1/commit")
    assert res.status_code == 200
    merge = next(p for q, p in session.calls if "MERGE (n:Entity" in q)
    assert merge["import_id"] == "B-1"  # stamped with its source


@pytest.mark.asyncio
async def test_ontology_additions_are_stamped_with_the_importing_source():
    from app.services.ontology_extension import _persist
    from app.services.provenance import ingest_source

    class S:
        def __init__(self):
            self.calls = []

        async def run(self, query, **params):
            self.calls.append(params)

    cls = [{"key": "PERSON.NEW", "parent_key": "PERSON"}]
    lnk = [{"type": "funded"}]

    s = S()
    with ingest_source("D-123"):
        await _persist(s, cls, lnk)
    assert s.calls[0]["rows"][0]["source_id"] == "D-123"
    assert s.calls[-1]["rows"][0]["source_id"] == "D-123"

    s = S()  # sandbox / manual paths run outside any import: left unstamped
    await _persist(s, cls, lnk)
    assert "source_id" not in s.calls[0]["rows"][0]
