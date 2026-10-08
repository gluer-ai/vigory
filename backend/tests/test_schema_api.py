"""Taxonomy-editor endpoints on /schema: edit label/notes, safe delete, merge.
Neo4j is faked with a small in-memory model of ClassDef + Entity nodes."""
import httpx
import pytest

from app.main import create_app


class _Result:
    def __init__(self, record=None, rows=None):
        self._record = record
        self._rows = rows or []

    async def single(self):
        return self._record

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row

        return gen()


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return _Ctx(self._session)


class _Session:
    def __init__(self, classes, entities):
        self.classes = {c["key"]: dict(c) for c in classes}
        self.entities = entities  # list of dicts

    async def run(self, query, **p):
        if "RETURN e.entity_subclass AS key, count(e)" in query:
            counts = {}
            for e in self.entities:
                counts[e["entity_subclass"]] = counts.get(e["entity_subclass"], 0) + 1
            return _Result(rows=[{"key": k, "n": n} for k, n in counts.items()])
        if "SET c += $changes" in query:
            self.classes[p["key"]].update(p["changes"])
            return _Result({"c": self.classes[p["key"]]})
        if "MATCH (c:ClassDef {key: $key}) RETURN c" in query:
            c = self.classes.get(p["key"])
            return _Result({"c": c} if c else None)
        if "OPTIONAL MATCH (ch:ClassDef {parent_key: $key})" in query:
            children = sum(1 for c in self.classes.values() if c.get("parent_key") == p["key"])
            ents = sum(1 for e in self.entities if e["entity_subclass"] == p["key"])
            return _Result({"children": children, "entities": ents})
        if "DETACH DELETE c" in query and "$into" in query:
            moved = [e for e in self.entities if e["entity_subclass"] == p["key"]]
            for e in moved:
                e["entity_subclass"], e["entity_class"] = p["into"], p["root"]
            del self.classes[p["key"]]
            return _Result({"moved": len(moved)})
        if "DETACH DELETE c" in query:
            del self.classes[p["key"]]
            return _Result()
        raise AssertionError(f"unexpected query: {query}")


CLASSES = [
    {"key": "ORGANIZATION", "parent_key": None, "level": 1, "label": "Organization"},
    {"key": "ORGANIZATION.INTEL", "parent_key": "ORGANIZATION", "level": 2, "label": "Intel"},
    {"key": "ORGANIZATION.INTEL.CIA", "parent_key": "ORGANIZATION.INTEL", "level": 3, "label": "CIA"},
    {"key": "ORGANIZATION.INTEL.AGENCY", "parent_key": "ORGANIZATION.INTEL", "level": 3, "label": "Agency"},
    {"key": "ORGANIZATION.EMPTY", "parent_key": "ORGANIZATION", "level": 2, "label": "Empty"},
    {"key": "PERSON", "parent_key": None, "level": 1, "label": "Person"},
    {"key": "PERSON.OFFICER", "parent_key": "PERSON", "level": 2, "label": "Officer"},
]


async def _client(monkeypatch, entities=None):
    from app.api import schema as schema_module

    session = _Session(CLASSES, entities if entities is not None else [])
    monkeypatch.setattr(schema_module, "get_driver", lambda: _Driver(session))
    transport = httpx.ASGITransport(app=create_app())
    return session, httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_counts_group_by_subclass(monkeypatch):
    ents = [{"entity_subclass": "ORGANIZATION.INTEL.CIA", "entity_class": "ORGANIZATION"}] * 2
    _, client = await _client(monkeypatch, ents)
    async with client:
        resp = await client.get("/schema/classes/counts")
    assert resp.json() == {"ORGANIZATION.INTEL.CIA": 2}


@pytest.mark.asyncio
async def test_patch_updates_label_and_clears_notes(monkeypatch):
    session, client = await _client(monkeypatch)
    session.classes["ORGANIZATION.EMPTY"]["notes"] = "old"
    async with client:
        resp = await client.patch(
            "/schema/classes/ORGANIZATION.EMPTY", json={"label": "Nothing", "notes": None}
        )
    assert resp.status_code == 200
    assert session.classes["ORGANIZATION.EMPTY"]["label"] == "Nothing"
    assert session.classes["ORGANIZATION.EMPTY"]["notes"] is None


@pytest.mark.asyncio
async def test_patch_rejects_null_or_empty_label_and_missing_class(monkeypatch):
    _, client = await _client(monkeypatch)
    async with client:
        assert (await client.patch("/schema/classes/PERSON", json={"label": None})).status_code == 422
        assert (await client.patch("/schema/classes/PERSON", json={"label": ""})).status_code == 422
        assert (await client.patch("/schema/classes/NOPE", json={"label": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_unused_leaf(monkeypatch):
    session, client = await _client(monkeypatch)
    async with client:
        resp = await client.delete("/schema/classes/ORGANIZATION.EMPTY")
    assert resp.status_code == 204
    assert "ORGANIZATION.EMPTY" not in session.classes


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ORGANIZATION", "ORGANIZATION.INTEL", "PERSON.OFFICER"])
async def test_delete_refuses_root_children_and_entities(monkeypatch, key):
    ents = [{"entity_subclass": "PERSON.OFFICER", "entity_class": "PERSON"}]
    session, client = await _client(monkeypatch, ents)
    async with client:
        resp = await client.delete(f"/schema/classes/{key}")
    assert resp.status_code == 409
    assert key in session.classes


@pytest.mark.asyncio
async def test_merge_moves_entities_and_deletes_duplicate(monkeypatch):
    ents = [
        {"entity_subclass": "ORGANIZATION.INTEL.CIA", "entity_class": "ORGANIZATION"},
        {"entity_subclass": "PERSON.OFFICER", "entity_class": "PERSON"},
    ]
    session, client = await _client(monkeypatch, ents)
    async with client:
        resp = await client.post(
            "/schema/classes/ORGANIZATION.INTEL.CIA/merge", json={"into": "ORGANIZATION.INTEL.AGENCY"}
        )
    assert resp.status_code == 200
    assert resp.json()["entities_moved"] == 1
    assert "ORGANIZATION.INTEL.CIA" not in session.classes
    assert ents[0]["entity_subclass"] == "ORGANIZATION.INTEL.AGENCY"
    assert ents[1]["entity_subclass"] == "PERSON.OFFICER"


@pytest.mark.asyncio
async def test_merge_into_other_root_updates_entity_class(monkeypatch):
    ents = [{"entity_subclass": "ORGANIZATION.EMPTY", "entity_class": "ORGANIZATION"}]
    _, client = await _client(monkeypatch, ents)
    async with client:
        resp = await client.post("/schema/classes/ORGANIZATION.EMPTY/merge", json={"into": "PERSON.OFFICER"})
    assert resp.status_code == 200
    assert ents[0] == {"entity_subclass": "PERSON.OFFICER", "entity_class": "PERSON"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,into,status",
    [
        ("PERSON.OFFICER", "PERSON.OFFICER", 422),                 # into itself
        ("PERSON.OFFICER", "NOPE", 404),                           # missing target
        ("NOPE", "PERSON.OFFICER", 404),                           # missing source
        ("PERSON", "ORGANIZATION", 409),                           # root
        ("ORGANIZATION.INTEL", "ORGANIZATION.INTEL.CIA", 422),     # own descendant
        ("ORGANIZATION.INTEL", "ORGANIZATION.EMPTY", 409),         # has children
    ],
)
async def test_merge_rejections_change_nothing(monkeypatch, key, into, status):
    session, client = await _client(monkeypatch)
    before = set(session.classes)
    async with client:
        resp = await client.post(f"/schema/classes/{key}/merge", json={"into": into})
    assert resp.status_code == status
    assert set(session.classes) == before
