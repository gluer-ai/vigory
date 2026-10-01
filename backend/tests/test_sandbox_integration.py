"""Sandbox isolation against a REAL Neo4j (skipped unless SANDBOX_TEST_NEO4J_URI
is set). Seed the ontology first:

  docker run -d --rm --name t -p 17687:7687 -e NEO4J_AUTH=neo4j/testpass123 neo4j:5-community
  NEO4J_URI=bolt://localhost:17687 NEO4J_PASSWORD=testpass123 python ontology/import_ontology.py
  SANDBOX_TEST_NEO4J_URI=bolt://localhost:17687 SANDBOX_TEST_NEO4J_PASSWORD=testpass123 pytest tests/test_sandbox_integration.py

Covers what mocks cannot: the Cypher itself keeps users apart and never
writes to the real graph.
"""
import json
import os

import httpx
import pytest
from neo4j import AsyncGraphDatabase

from app import auth
from app.api import auth as auth_api
from app.api import sandbox as sandbox_api
from app.config import Settings
from app.main import create_app
from app.services import sandbox as svc

URI = os.environ.get("SANDBOX_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(not URI, reason="needs SANDBOX_TEST_NEO4J_URI (a throwaway Neo4j)")

ENTITIES = [
    ("P-1", "PERSON.MILITARY_PERSONNEL", "Ivan"),
    ("O-1", "ORGANIZATION.COMMERCIAL_ENTITY.FINANCIAL_INSTITUTION", "Acme Bank"),
    ("L-1", "LOCATION.ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT", "Oslo"),
    ("L-2", "LOCATION.ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT", "Far Away"),  # not near P-1
]
LINKS = [("LK-1", "commands", "P-1", "O-1"), ("LK-2", "headquartered_in", "O-1", "L-1")]


@pytest.fixture
async def driver():
    d = AsyncGraphDatabase.driver(URI, auth=("neo4j", os.environ.get("SANDBOX_TEST_NEO4J_PASSWORD", "testpass123")))
    async with d.session() as s:
        await s.run("MATCH (x) WHERE x:Entity OR x:Sandbox DETACH DELETE x")
        for eid, sub, label in ENTITIES:
            await s.run(
                "CREATE (:Entity {entity_id:$id, entity_class:$cls, entity_subclass:$sub, label:$label,"
                " aliases:[], status:'active', confidence:'B2', source_ref:'t', attrs:'{}'})",
                id=eid, cls=sub.split(".")[0], sub=sub, label=label)
        for lid, lt, a, b in LINKS:
            await s.run(
                "MATCH (a:Entity {entity_id:$a}), (b:Entity {entity_id:$b})"
                " CREATE (a)-[:LINK {link_id:$id, link_type:$lt, source_entity:$a, target_entity:$b}]->(b)",
                id=lid, lt=lt, a=a, b=b)
    yield d
    async with d.session() as s:
        await s.run("MATCH (x) WHERE x:Entity OR x:Sandbox DETACH DELETE x")
    await d.close()


async def real_graph(driver) -> str:
    async with driver.session() as s:
        ents = [dict(r["e"]) async for r in await s.run("MATCH (e:Entity) RETURN e ORDER BY e.entity_id")]
        links = [dict(r["r"]) async for r in await s.run("MATCH ()-[r:LINK]->() RETURN r ORDER BY r.link_id")]
    return json.dumps({"e": ents, "l": links}, sort_keys=True)


@pytest.fixture
def api(driver, monkeypatch):
    settings = Settings(auth_mode="platform", voice_api_url="https://p.example.com")
    for mod in (auth, auth_api):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    async def who(settings, token, *a, **k):
        return {"alice-token": "1", "bob-token": "2"}.get(token)

    monkeypatch.setattr(auth, "resolve_platform_user", who)
    monkeypatch.setattr(sandbox_api, "get_driver", lambda: driver)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t")
    client.alice = {"Authorization": "Bearer alice-token"}
    client.bob = {"Authorization": "Bearer bob-token"}
    return client


async def test_requires_sign_in(api):
    assert (await api.get("/sandbox")).status_code == 401
    assert (await api.get("/sandbox", headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_seed_copies_neighbourhood_read_only(api, driver):
    before = await real_graph(driver)
    r = await api.post("/sandbox", json={"name": "S1", "trigger_entity_id": "P-1", "hops": 2}, headers=api.alice)
    assert r.status_code == 201
    sb = r.json()
    assert {n["id"] for n in sb["nodes"]} == {"P-1", "O-1", "L-1"}  # L-2 is out of range
    assert {e["id"] for e in sb["edges"]} == {"LK-1", "LK-2"}
    trigger = next(n for n in sb["nodes"] if n["id"] == "P-1")
    assert (trigger["x"], trigger["y"]) == (0.0, 0.0) and trigger["base"]["label"] == "Ivan"
    assert sb["version"] == 1 and sb["node_count"] == 3
    assert await real_graph(driver) == before


async def test_unknown_trigger_is_404_and_hops_limit_applies(api):
    r = await api.post("/sandbox", json={"name": "x", "trigger_entity_id": "NOPE"}, headers=api.alice)
    assert r.status_code == 404
    r = await api.post("/sandbox", json={"name": "x", "trigger_entity_id": "P-1", "hops": 1}, headers=api.alice)
    assert {n["id"] for n in r.json()["nodes"]} == {"P-1", "O-1"}


async def test_users_cannot_see_or_touch_each_others_sandboxes(api):
    sb = (await api.post("/sandbox", json={"name": "Alice's", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    sid = sb["sandbox_id"]

    assert [s["sandbox_id"] for s in (await api.get("/sandbox", headers=api.alice)).json()] == [sid]
    assert (await api.get("/sandbox", headers=api.bob)).json() == []

    body = {"nodes": sb["nodes"], "edges": sb["edges"], "version": 1, "name": "hijacked"}
    assert (await api.get(f"/sandbox/{sid}", headers=api.bob)).status_code == 404
    assert (await api.put(f"/sandbox/{sid}", json=body, headers=api.bob)).status_code == 404
    assert (await api.delete(f"/sandbox/{sid}", headers=api.bob)).status_code == 404

    mine = (await api.get(f"/sandbox/{sid}", headers=api.alice)).json()
    assert mine["name"] == "Alice's" and mine["version"] == 1  # untouched by bob's attempts


async def test_edits_persist_and_never_reach_real_data(api, driver):
    before = await real_graph(driver)
    sb = (await api.post("/sandbox", json={"name": "S", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    nodes, edges = sb["nodes"], sb["edges"]

    next(n for n in nodes if n["id"] == "P-1").update(x=420.5, y=-33.0, label="Ivan (renamed)")
    next(n for n in nodes if n["id"] == "O-1")["entity_subclass"] = "ORGANIZATION.COMMERCIAL_ENTITY"
    nodes.append({"id": "N-new1", "origin": "new", "label": "Proposed Unit", "x": 5, "y": 6,
                  "entity_subclass": "ORGANIZATION.COMMERCIAL_ENTITY.FINANCIAL_INSTITUTION", "base": None})
    next(e for e in edges if e["id"] == "LK-1")["link_type"] = "funds"        # change a relation
    edges[:] = [e for e in edges if e["id"] != "LK-2"]                         # remove a relation
    edges.append({"id": "E-new1", "origin": "new", "source": "N-new1", "target": "O-1",
                  "link_type": "affiliated_with", "base_type": None})

    r = await api.put(f"/sandbox/{sb['sandbox_id']}", json={"nodes": nodes, "edges": edges, "version": 1, "name": "Renamed"}, headers=api.alice)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2

    got = (await api.get(f"/sandbox/{sb['sandbox_id']}", headers=api.alice)).json()
    assert got["name"] == "Renamed" and got["node_count"] == 4 and got["edge_count"] == 2
    ivan = next(n for n in got["nodes"] if n["id"] == "P-1")
    assert (ivan["x"], ivan["y"], ivan["label"]) == (420.5, -33.0, "Ivan (renamed)")
    assert ivan["base"]["label"] == "Ivan"  # original kept for "what changed"
    assert {e["id"]: e["link_type"] for e in got["edges"]} == {"LK-1": "funds", "E-new1": "affiliated_with"}

    assert await real_graph(driver) == before  # the real graph is byte-for-byte unchanged

    await api.delete(f"/sandbox/{sb['sandbox_id']}", headers=api.alice)
    assert await real_graph(driver) == before
    assert (await api.get("/sandbox", headers=api.alice)).json() == []


async def test_stale_save_is_rejected_with_409(api):
    sb = (await api.post("/sandbox", json={"name": "S", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    body = {"nodes": sb["nodes"], "edges": sb["edges"], "version": 1}
    assert (await api.put(f"/sandbox/{sb['sandbox_id']}", json=body, headers=api.alice)).status_code == 200
    assert (await api.put(f"/sandbox/{sb['sandbox_id']}", json=body, headers=api.alice)).status_code == 409


@pytest.mark.parametrize("mutate,fragment", [
    (lambda n, e: n[0].update(entity_subclass="PERSON.MADE_UP"), "not a known class"),
    (lambda n, e: e[0].update(link_type="made_up_link"), "not a known link type"),
    (lambda n, e: e[0].update(target="GHOST"), "not in the sandbox"),
    (lambda n, e: e[0].update(target=e[0]["source"]), "itself"),
    (lambda n, e: e.append(dict(e[0], id="dup")) or e[-1].update(id=e[0]["id"]), "Duplicate link id"),
    (lambda n, e: n.append(dict(n[0])), "Duplicate entity id"),
])
async def test_invalid_edits_are_rejected(api, mutate, fragment):
    sb = (await api.post("/sandbox", json={"name": "S", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    nodes, edges = sb["nodes"], sb["edges"]
    mutate(nodes, edges)
    r = await api.put(f"/sandbox/{sb['sandbox_id']}", json={"nodes": nodes, "edges": edges, "version": 1}, headers=api.alice)
    assert r.status_code == 422 and fragment in r.json()["detail"]


async def test_relation_must_respect_domain_and_range(api):
    sb = (await api.post("/sandbox", json={"name": "S", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    # headquartered_in is Organization -> Location; a Person as its source must fail.
    edges = sb["edges"] + [{"id": "E-bad", "origin": "new", "source": "P-1", "target": "L-1",
                            "link_type": "headquartered_in", "base_type": None}]
    r = await api.put(f"/sandbox/{sb['sandbox_id']}", json={"nodes": sb["nodes"], "edges": edges, "version": 1}, headers=api.alice)
    assert r.status_code == 422 and "cannot use a person entity" in r.json()["detail"]


async def test_per_user_limit(api, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PER_USER", 2)
    for i in range(2):
        assert (await api.post("/sandbox", json={"name": f"s{i}"}, headers=api.alice)).status_code == 201
    assert (await api.post("/sandbox", json={"name": "third"}, headers=api.alice)).status_code == 422
    assert (await api.post("/sandbox", json={"name": "bobs"}, headers=api.bob)).status_code == 201  # per user


async def test_sandboxes_are_invisible_to_real_data_queries(api, driver):
    await api.post("/sandbox", json={"name": "S", "trigger_entity_id": "P-1"}, headers=api.alice)
    from app.services.search import search_entities_by_name

    async with driver.session() as s:
        assert len([r async for r in await s.run("MATCH (e:Entity) RETURN e")]) == len(ENTITIES)
        assert [e["label"] for e in await search_entities_by_name(s, "Ivan")] == ["Ivan"]
        assert [r async for r in await s.run("MATCH ()-[r:LINK]->() RETURN r")].__len__() == len(LINKS)


async def test_nested_structure_round_trips_and_bad_nesting_is_refused(api, driver):
    before = await real_graph(driver)
    sb = (await api.post("/sandbox", json={"name": "Nested", "trigger_entity_id": "P-1"}, headers=api.alice)).json()
    sid, nodes, edges = sb["sandbox_id"], sb["nodes"], sb["edges"]
    assert all(n["parent"] is None for n in nodes + edges)

    leaf = "LOCATION.ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT"
    nodes += [
        {"id": "N-a", "origin": "new", "label": "Inside Ivan", "entity_subclass": leaf, "x": 1, "y": 2, "base": None, "parent": "P-1"},
        {"id": "N-b", "origin": "new", "label": "Inside the link", "entity_subclass": leaf, "x": 3, "y": 4, "base": None, "parent": "LK-1"},
        {"id": "N-c", "origin": "new", "label": "Two deep", "entity_subclass": leaf, "x": 5, "y": 6, "base": None, "parent": "N-a"},
        {"id": "N-d", "origin": "new", "label": "Two deep too", "entity_subclass": leaf, "x": 7, "y": 8, "base": None, "parent": "N-a"},
    ]
    edges.append({"id": "E-in", "origin": "new", "source": "N-c", "target": "N-d", "link_type": "adjacent_to", "base_type": None, "parent": "N-a"})
    r = await api.put(f"/sandbox/{sid}", json={"nodes": nodes, "edges": edges, "version": 1}, headers=api.alice)
    assert r.status_code == 200, r.text

    got = (await api.get(f"/sandbox/{sid}", headers=api.alice)).json()
    assert {n["id"]: n["parent"] for n in got["nodes"] if n["origin"] == "new"} == {"N-a": "P-1", "N-b": "LK-1", "N-c": "N-a", "N-d": "N-a"}
    assert next(e for e in got["edges"] if e["id"] == "E-in")["parent"] == "N-a"

    # a dangling parent, and a link that crosses levels, are refused with 422
    bad_nodes = [dict(n) for n in got["nodes"]]
    next(n for n in bad_nodes if n["id"] == "N-b")["parent"] = "GONE"
    r = await api.put(f"/sandbox/{sid}", json={"nodes": bad_nodes, "edges": got["edges"], "version": 2}, headers=api.alice)
    assert r.status_code == 422 and "not in the sandbox" in r.json()["detail"]
    crossing = got["edges"] + [{"id": "E-x", "origin": "new", "source": "P-1", "target": "N-a", "link_type": "same_as", "base_type": None, "parent": None}]
    r = await api.put(f"/sandbox/{sid}", json={"nodes": got["nodes"], "edges": crossing, "version": 2}, headers=api.alice)
    assert r.status_code == 422 and "same level" in r.json()["detail"]

    assert await real_graph(driver) == before


# ---- assistant endpoints ------------------------------------------------------------


async def _canvas(api, **kw):
    sb = (await api.post("/sandbox", json={"name": "A", "trigger_entity_id": "P-1", **kw}, headers=api.alice)).json()
    return sb


async def test_agent_endpoints_require_sign_in(api):
    for path in ("/sandbox/agent/chat", "/sandbox/agent/ops", "/sandbox/agent/describe"):
        assert (await api.post(path, json={})).status_code == 401


async def test_agent_ops_edits_the_returned_canvas_only(api, driver):
    before = await real_graph(driver)
    sb = await _canvas(api)
    body = {"nodes": sb["nodes"], "edges": sb["edges"], "container": None, "ops": [
        {"op": "rename_entity", "entity": "Ivan", "label": "Ivan the Great"},
        {"op": "copy_from_graph", "label": "Far Away"},
        {"op": "add_link", "source": "Ivan the Great", "target": "Oslo", "link_type": "commands"},  # invalid pair
    ]}
    r = await api.post("/sandbox/agent/ops", json=body, headers=api.alice)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["ok"] for x in out["results"]] == [True, True, False]
    assert "Valid types" in out["results"][2]["message"]
    assert {n["id"]: n["label"] for n in out["nodes"]}["P-1"] == "Ivan the Great"
    assert any(n["id"] == "L-2" and n["origin"] == "graph" for n in out["nodes"])
    assert "Ivan the Great" in out["summary"] and "ON SCREEN NOW: top level" in out["summary"]

    # nothing was saved to the sandbox and nothing touched the real graph
    stored = (await api.get(f"/sandbox/{sb['sandbox_id']}", headers=api.alice)).json()
    assert stored["version"] == 1 and {n["id"]: n["label"] for n in stored["nodes"]}["P-1"] == "Ivan"
    assert await real_graph(driver) == before


async def test_agent_chat_runs_the_model_and_validates_what_it_proposes(api, driver, monkeypatch):
    from app.services import sandbox_agent as ag

    async def fake_llm(system, user):
        return {"reply": "Done.", "ops": [
            {"op": "add_entity", "label": "Proposed Unit", "entity_subclass": "ORGANIZATION.COMMERCIAL_ENTITY"},
            {"op": "remove_entity", "entity": "Nobody"},
        ]} if "Proposed Unit" not in user else {"reply": "Could not remove Nobody.", "ops": []}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    before = await real_graph(driver)
    sb = await _canvas(api)
    r = await api.post("/sandbox/agent/chat", headers=api.alice, json={
        "nodes": sb["nodes"], "edges": sb["edges"], "container": None,
        "message": "add a proposed unit and remove nobody", "history": [{"role": "user", "text": "hi"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed"] and any(n["label"] == "Proposed Unit" and n["origin"] == "new" for n in out["nodes"])
    assert [x["ok"] for x in out["results"]] == [True, False]
    assert await real_graph(driver) == before


async def test_agent_describe_and_input_limits(api):
    sb = await _canvas(api)
    r = await api.post("/sandbox/agent/describe", json={"nodes": sb["nodes"], "edges": sb["edges"], "container": "P-1"}, headers=api.alice)
    assert "inside \"Ivan\"" in r.json()["summary"]
    big = {"nodes": sb["nodes"], "edges": sb["edges"], "message": "x" * 2001}
    assert (await api.post("/sandbox/agent/chat", json=big, headers=api.alice)).status_code == 422
    empty = {"nodes": sb["nodes"], "edges": sb["edges"], "ops": []}
    assert (await api.post("/sandbox/agent/ops", json=empty, headers=api.alice)).status_code == 422
