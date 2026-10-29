"""Deleting an import source, against a real Neo4j (see docker-compose.yml).

Every node this file creates carries a unique per-test prefix and the fixture
removes exactly those nodes afterwards, so it is safe against a dev database.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.main import create_app
from app.services.source_cleanup import SourceBusyError, delete_source, plan_removal


@pytest_asyncio.fixture
async def graph(monkeypatch, tmp_path):
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception:
        await driver.close()
        pytest.skip("Neo4j is not reachable")

    p = f"T{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    from app.api import documents as documents_module
    from app.api import ingest as ingest_module

    for module in (documents_module, ingest_module):
        monkeypatch.setattr(module, "get_driver", lambda: driver)

    async with driver.session() as session:
        yield SimpleNamespace(driver=driver, session=session, p=p, upload_dir=tmp_path)
        await session.run("MATCH (n) WHERE n.entity_id STARTS WITH $p DETACH DELETE n", p=p)
        await session.run("MATCH (n:IngestBatch) WHERE n.batch_id STARTS WITH $p DETACH DELETE n", p=p)
        await session.run("MATCH (n:Document) WHERE n.document_id STARTS WITH $p DETACH DELETE n", p=p)
        # class keys look like "PERSON.<PREFIX>_NAME": match the prefix after the root
        await session.run("MATCH (n:ClassDef) WHERE n.key CONTAINS $p DETACH DELETE n", p=f".{p.upper()}_")
        await session.run("MATCH (n:LinkDef) WHERE n.type STARTS WITH $p DETACH DELETE n", p=p.lower())
    await driver.close()


def ent(g, name, label=None, sub="PERSON.X", **extra):
    return {
        "entity_id": f"{g.p}-{name}", "entity_class": "PERSON", "entity_subclass": sub,
        "label": label or name, "aliases": [], "status": "active", "confidence": "B2",
        "source_ref": "t", "attrs": {}, **extra,
    }


def lnk(g, name, src, tgt, t="associated_with"):
    return {
        "link_id": f"{g.p}-{name}", "link_type": t, "source_entity": f"{g.p}-{src}",
        "target_entity": f"{g.p}-{tgt}", "direction": "directed", "confidence": "B2",
        "source_ref": "t", "assertion_status": "reported", "attrs": {},
    }


async def make_import(g, doc, entities, links, *, status="proposed", classes=(), link_types=()):
    """A Document + its review batch, as document ingestion leaves them."""
    did = f"{g.p}-{doc}"
    bid = f"{g.p}-B{doc}"
    await g.session.run(
        "CREATE (:Document {document_id: $d, filename: $f, status: $s, uploaded_at: $now, batch_id: $b})",
        d=did, f=f"{doc}.txt", s=status, now=datetime.now(timezone.utc).isoformat(), b=bid,
    )
    await g.session.run(
        """CREATE (:IngestBatch {batch_id: $b, status: 'proposed', source_text: 't', document_id: $d,
                   entities: $e, links: $l, rejected_entities: '[]', rejected_links: '[]'})""",
        b=bid, d=did, e=json.dumps(entities), l=json.dumps(links),
    )
    for key, parent, level in classes:  # ontology terms the extraction added for this document
        await g.session.run(
            "CREATE (:ClassDef {key: $k, parent_key: $p, level: $lv, label: $k, origin: 'extraction', source_id: $d})",
            k=key, p=parent, lv=level, d=did,
        )
    for t in link_types:
        await g.session.run(
            "CREATE (:LinkDef {type: $t, domain: 'Any', range: 'Any', origin: 'extraction', source_id: $d})",
            t=t, d=did,
        )
    return did, bid


async def api(method, path, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        return await c.request(method, path, **kw)


async def count(g, query, **params):
    res = await g.session.run(query, **params)
    return (await res.single())["n"]


async def commit(g, bid):
    res = await api("POST", f"/ingest/{bid}/commit")
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_commit_stamps_source_and_source_plus_data_removal_cleans_everything(graph):
    g = graph
    cls = f"{g.p.upper()}_NEWKIND"
    ltype = f"{g.p.lower()}_funds"
    ents = [ent(g, "a", sub=f"PERSON.{cls}"), ent(g, "b")]
    links = [lnk(g, "L1", "a", "b", ltype)]
    did, bid = await make_import(
        g, "d1", ents, links, classes=[(f"PERSON.{cls}", "PERSON", 2)], link_types=[ltype]
    )
    (g.upload_dir / did).mkdir()
    (g.upload_dir / did / "d1.txt").write_text("x")
    await commit(g, bid)

    # stamped with the DOCUMENT id (not the batch id)
    assert await count(g, "MATCH (e:Entity {source_id: $d}) RETURN count(e) AS n", d=did) == 2
    assert await count(g, "MATCH ()-[r:LINK {source_id: $d}]->() RETURN count(r) AS n", d=did) == 1

    preview = (await api("GET", f"/documents/{did}/removal-preview")).json()
    assert preview["entities"]["remove"] == 2 and preview["links"]["remove"] == 1
    assert preview["classes"]["remove"] == [f"PERSON.{cls}"]
    assert preview["link_types"]["remove"] == [ltype]
    assert await count(g, "MATCH (e:Entity {source_id: $d}) RETURN count(e) AS n", d=did) == 2  # preview is read-only

    res = await api("DELETE", f"/documents/{did}", params={"remove_data": "true"})
    assert res.status_code == 200 and res.json()["data_removed"] is True
    assert await count(g, "MATCH (e:Entity) WHERE e.entity_id STARTS WITH $p RETURN count(e) AS n", p=g.p) == 0
    assert await count(g, "MATCH (c:ClassDef {key: $k}) RETURN count(c) AS n", k=f"PERSON.{cls}") == 0
    assert await count(g, "MATCH (l:LinkDef {type: $t}) RETURN count(l) AS n", t=ltype) == 0
    assert await count(g, "MATCH (d:Document {document_id: $d}) RETURN count(d) AS n", d=did) == 0
    assert await count(g, "MATCH (b:IngestBatch {batch_id: $b}) RETURN count(b) AS n", b=bid) == 0
    assert not (g.upload_dir / did).exists()


@pytest.mark.asyncio
async def test_source_only_removal_keeps_the_data_but_unlinks_it_from_the_source(graph):
    g = graph
    cls = f"{g.p.upper()}_KEEPME"
    did, bid = await make_import(
        g, "d1", [ent(g, "a", sub=f"PERSON.{cls}"), ent(g, "b")], [lnk(g, "L1", "a", "b")],
        classes=[(f"PERSON.{cls}", "PERSON", 2)],
    )
    await commit(g, bid)

    res = await api("DELETE", f"/documents/{did}")  # default: remove_data=false
    assert res.status_code == 200 and res.json()["data_removed"] is False

    assert await count(g, "MATCH (e:Entity) WHERE e.entity_id STARTS WITH $p RETURN count(e) AS n", p=g.p) == 2
    assert await count(g, "MATCH ()-[r:LINK]->() WHERE r.link_id STARTS WITH $p RETURN count(r) AS n", p=g.p) == 1
    assert await count(g, "MATCH (c:ClassDef {key: $k}) RETURN count(c) AS n", k=f"PERSON.{cls}") == 1
    assert await count(g, "MATCH (n) WHERE n.source_id = $d RETURN count(n) AS n", d=did) == 0
    assert await count(g, "MATCH (d:Document {document_id: $d}) RETURN count(d) AS n", d=did) == 0


@pytest.mark.asyncio
async def test_entity_that_another_source_links_to_is_kept(graph):
    g = graph
    d1, b1 = await make_import(g, "d1", [ent(g, "a"), ent(g, "shared")], [lnk(g, "L1", "a", "shared")])
    d2, b2 = await make_import(g, "d2", [ent(g, "c")], [lnk(g, "L2", "c", "shared")])
    await commit(g, b1)
    # d2's link points at d1's entity: the entity must survive d1's removal.
    await commit(g, b2)

    plan = await plan_removal(g.session, {"document_id": d1, "filename": "d1.txt", "status": "committed"})
    assert plan.kept_shared_entities == [f"{g.p}-shared"]
    assert plan.entity_ids == [f"{g.p}-a"]

    await api("DELETE", f"/documents/{d1}", params={"remove_data": "true"})
    ids = {r["id"] async for r in await g.session.run(
        "MATCH (e:Entity) WHERE e.entity_id STARTS WITH $p RETURN e.entity_id AS id", p=g.p)}
    assert ids == {f"{g.p}-shared", f"{g.p}-c"}
    # d2's own link survived; d1's link is gone
    links = {r["id"] async for r in await g.session.run(
        "MATCH ()-[r:LINK]->() WHERE r.link_id STARTS WITH $p RETURN r.link_id AS id", p=g.p)}
    assert links == {f"{g.p}-L2"}
    # the kept entity no longer claims a deleted source
    assert await count(g, "MATCH (e:Entity {entity_id: $i}) WHERE e.source_id IS NULL RETURN count(e) AS n", i=f"{g.p}-shared") == 1


@pytest.mark.asyncio
async def test_ontology_terms_still_in_use_are_kept(graph):
    g = graph
    cls = f"{g.p.upper()}_SHARED"
    did, bid = await make_import(
        g, "d1", [ent(g, "a", sub=f"PERSON.{cls}")], [], classes=[(f"PERSON.{cls}", "PERSON", 2)]
    )
    await commit(g, bid)
    # someone else (manual entry) later used the AI-added class
    await g.session.run(
        "CREATE (:Entity {entity_id: $i, entity_class: 'PERSON', entity_subclass: $s, label: 'manual'})",
        i=f"{g.p}-manual", s=f"PERSON.{cls}",
    )
    plan = await plan_removal(g.session, {"document_id": did, "filename": "x", "status": "committed"})
    assert plan.classes == []
    assert plan.kept_classes[0]["key"] == f"PERSON.{cls}"
    await api("DELETE", f"/documents/{did}", params={"remove_data": "true"})
    assert await count(g, "MATCH (c:ClassDef {key: $k}) RETURN count(c) AS n", k=f"PERSON.{cls}") == 1
    assert await count(g, "MATCH (e:Entity {entity_id: $i}) RETURN count(e) AS n", i=f"{g.p}-manual") == 1


@pytest.mark.asyncio
async def test_import_from_before_source_stamping_is_traced_through_its_batch(graph):
    g = graph
    did, bid = await make_import(g, "old", [ent(g, "a"), ent(g, "b")], [lnk(g, "L1", "a", "b")])
    await g.session.run("MATCH (b:IngestBatch {batch_id: $b}) SET b.status = 'committed'", b=bid)
    for e in (ent(g, "a"), ent(g, "b")):  # committed by the old code: no source_id
        await g.session.run(
            "CREATE (:Entity {entity_id: $i, entity_class: 'PERSON', entity_subclass: 'PERSON.X', label: $l})",
            i=e["entity_id"], l=e["label"],
        )
    await g.session.run(
        """MATCH (a:Entity {entity_id: $a}), (b:Entity {entity_id: $b})
           CREATE (a)-[:LINK {link_id: $l, link_type: 'associated_with'}]->(b)""",
        a=f"{g.p}-a", b=f"{g.p}-b", l=f"{g.p}-L1",
    )
    preview = (await api("GET", f"/documents/{did}/removal-preview")).json()
    assert preview["entities"]["remove"] == 2 and preview["entities"]["untagged_import"] == 2
    assert preview["links"]["remove"] == 1

    # An entity id that ANOTHER committed import also wrote is ambiguous: never claimed.
    other_d, other_b = await make_import(g, "other", [ent(g, "b")], [])
    await g.session.run("MATCH (b:IngestBatch {batch_id: $b}) SET b.status = 'committed'", b=other_b)
    preview = (await api("GET", f"/documents/{did}/removal-preview")).json()
    assert preview["entities"]["remove"] == 1  # only 'a'
    await api("DELETE", f"/documents/{did}", params={"remove_data": "true"})
    left = {r["id"] async for r in await g.session.run(
        "MATCH (e:Entity) WHERE e.entity_id STARTS WITH $p RETURN e.entity_id AS id", p=g.p)}
    assert left == {f"{g.p}-b"}


@pytest.mark.asyncio
async def test_processing_document_cannot_be_deleted_unless_its_job_died(graph):
    g = graph
    did, _ = await make_import(g, "d1", [], [], status="processing")
    res = await api("DELETE", f"/documents/{did}", params={"remove_data": "true"})
    assert res.status_code == 409
    res = await api("GET", f"/documents/{did}/removal-preview")
    assert res.status_code == 409
    assert await count(g, "MATCH (d:Document {document_id: $d}) RETURN count(d) AS n", d=did) == 1

    # No progress for over 30 minutes: the background job is gone, so allow cleanup.
    stale = datetime.now(timezone.utc) + timedelta(hours=1)
    doc = {"document_id": did, "filename": "x", "status": "processing", "uploaded_at": datetime.now(timezone.utc).isoformat()}
    with pytest.raises(SourceBusyError):
        await delete_source(g.session, doc, remove_data=False, upload_dir=str(g.upload_dir))
    await delete_source(g.session, doc, remove_data=False, upload_dir=str(g.upload_dir), now=stale)
    assert await count(g, "MATCH (d:Document {document_id: $d}) RETURN count(d) AS n", d=did) == 0


@pytest.mark.asyncio
async def test_delete_unknown_document_404s(graph):
    assert (await api("DELETE", "/documents/D-nope")).status_code == 404
    assert (await api("GET", "/documents/D-nope/removal-preview")).status_code == 404


@pytest.mark.asyncio
async def test_commit_overwriting_guard_and_keep_separate_end_to_end(graph):
    """The extraction-minted-id hazard: a second import reusing an id must not overwrite."""
    g = graph
    d1, b1 = await make_import(g, "d1", [ent(g, "temp1", label="First Person")], [])
    await commit(g, b1)
    d2, b2 = await make_import(g, "d2", [ent(g, "temp1", label="Totally Different")], [lnk(g, "L9", "temp1", "temp1")])

    res = await api("POST", f"/ingest/{b2}/commit")
    assert res.status_code == 409 and "duplicate" in res.json()["detail"]
    assert await count(g, "MATCH (e:Entity {entity_id: $i, label: 'First Person'}) RETURN count(e) AS n", i=f"{g.p}-temp1") == 1

    dups = (await api("GET", f"/ingest/{b2}/duplicates")).json()["duplicates"]
    assert dups[0]["matches"][0]["reason"] == "same_id"
    res = await api(
        "POST", f"/ingest/{b2}/duplicates/resolve",
        json={"decisions": [{"entity_id": f"{g.p}-temp1", "action": "keep_separate"}]},
    )
    assert res.status_code == 200
    assert res.json()["entities"][0]["entity_id"] == f"{g.p}-temp1-2"
    await commit(g, b2)
    assert await count(g, "MATCH (e:Entity {entity_id: $i, label: 'First Person'}) RETURN count(e) AS n", i=f"{g.p}-temp1") == 1
    assert await count(g, "MATCH (e:Entity {entity_id: $i, label: 'Totally Different'}) RETURN count(e) AS n", i=f"{g.p}-temp1-2") == 1


@pytest.mark.asyncio
async def test_untagged_trace_never_claims_an_entity_owned_by_another_source(graph):
    g = graph
    did, bid = await make_import(g, "old", [ent(g, "a"), ent(g, "mine")], [])
    await g.session.run("MATCH (b:IngestBatch {batch_id: $b}) SET b.status = 'committed'", b=bid)
    await g.session.run(
        "CREATE (:Entity {entity_id: $i, entity_class: 'PERSON', entity_subclass: 'PERSON.X', label: 'a', source_id: 'D-someone-else'})",
        i=f"{g.p}-a",
    )
    await g.session.run(
        "CREATE (:Entity {entity_id: $i, entity_class: 'PERSON', entity_subclass: 'PERSON.X', label: 'mine'})",
        i=f"{g.p}-mine",
    )
    await api("DELETE", f"/documents/{did}", params={"remove_data": "true"})
    left = {r["id"] async for r in await g.session.run(
        "MATCH (e:Entity) WHERE e.entity_id STARTS WITH $p RETURN e.entity_id AS id", p=g.p)}
    assert left == {f"{g.p}-a"}  # the other source's entity survives
    await g.session.run("MATCH (e:Entity {entity_id: $i}) DETACH DELETE e", i=f"{g.p}-a")
