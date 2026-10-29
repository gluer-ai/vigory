"""Delete an import source (an uploaded Document), optionally with its data.

Two modes:
  source only     remove the Document, its review batch and the stored file;
                  everything the import put in the graph stays, no longer
                  tied to a source
  source + data   also remove what the import created: committed entities and
                  links, and ontology classes / link types its extraction added

What counts as "created by this source" is the `source_id` stamped at commit /
ontology-growth time (see app.services.provenance). Imports from before that
stamping existed are traced through the committed batch's own record of the
entity/link ids it wrote - and only where no other import's batch wrote the
same id, because extraction mints ids like "P-temp1" that different imports
can reuse.

Safety rules (the plan is computed first and shown to the user as a preview):
  - an entity another source (or a hand-made / feed link) still links to is
    KEPT, not deleted - removing it would cut that other data
  - an ontology term is only removed once nothing uses it: no remaining
    entity/link of that type, no subclass under it, no other pending proposal
  - a document still being extracted cannot be deleted (unless its job has
    clearly died: no progress update for STALE_PROCESSING)
  - the Document node is deleted LAST, so a failed run can simply be retried
"""
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from neo4j import AsyncSession

STALE_PROCESSING = timedelta(minutes=30)
_SAMPLE = 5


class SourceBusyError(Exception):
    """The document is still being extracted."""


def _loads(value: object) -> list[dict]:
    try:
        parsed = json.loads(value or "[]")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    return [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []


def _triple(link: dict) -> tuple[str, str, str]:
    return (link.get("link_id", ""), link.get("source_entity", ""), link.get("target_entity", ""))


def ensure_not_busy(doc: dict, now: datetime | None = None) -> None:
    if doc.get("status") != "processing":
        return
    now = now or datetime.now(timezone.utc)
    stamp = doc.get("updated_at") or doc.get("uploaded_at")
    try:
        last = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        last = now  # unknown age: assume it is alive
    if now - last < STALE_PROCESSING:
        raise SourceBusyError("this document is still being processed - wait for it to finish")


@dataclass
class RemovalPlan:
    document_id: str
    filename: str
    status: str
    entity_ids: list[str] = field(default_factory=list)
    entity_labels: list[str] = field(default_factory=list)
    kept_shared_entities: list[str] = field(default_factory=list)
    links: list[tuple[str, str, str]] = field(default_factory=list)
    legacy_entities: int = 0
    legacy_links: int = 0
    classes: list[str] = field(default_factory=list)
    kept_classes: list[dict] = field(default_factory=list)
    link_types: list[str] = field(default_factory=list)
    kept_link_types: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "status": self.status,
            "entities": {
                "remove": len(self.entity_ids),
                "sample": self.entity_labels,
                "kept_shared": len(self.kept_shared_entities),
                "untagged_import": self.legacy_entities,
            },
            "links": {"remove": len(self.links), "untagged_import": self.legacy_links},
            "classes": {"remove": self.classes, "kept": self.kept_classes},
            "link_types": {"remove": self.link_types, "kept": self.kept_link_types},
        }


async def _rows(session: AsyncSession, query: str, **params) -> list[dict]:
    result = await session.run(query, **params)
    return [dict(r) async for r in result]


async def plan_removal(session: AsyncSession, doc: dict) -> RemovalPlan:
    """Work out what source+data removal would delete and what it must keep.
    Read-only."""
    sid = doc["document_id"]
    plan = RemovalPlan(sid, doc.get("filename", ""), doc.get("status", ""))

    # ---- what this source created ------------------------------------------------
    owned_entities: set[str] = {
        r["id"] for r in await _rows(session, "MATCH (e:Entity {source_id: $sid}) RETURN e.entity_id AS id", sid=sid)
    }
    owned_links: set[tuple[str, str, str]] = {
        (r["lid"], r["src"], r["tgt"])
        for r in await _rows(
            session,
            """
            MATCH (s:Entity)-[r:LINK {source_id: $sid}]->(t:Entity)
            RETURN r.link_id AS lid, s.entity_id AS src, t.entity_id AS tgt
            """,
            sid=sid,
        )
    }

    mine = await _rows(session, "MATCH (b:IngestBatch {document_id: $id, status: 'committed'}) RETURN b.entities AS e, b.links AS l", id=sid)
    if mine:
        others = await _rows(
            session,
            "MATCH (b:IngestBatch {status: 'committed'}) WHERE coalesce(b.document_id, '') <> $id RETURN b.entities AS e, b.links AS l",
            id=sid,
        )
        other_ids = {e.get("entity_id") for o in others for e in _loads(o["e"])}
        other_triples = {_triple(l) for o in others for l in _loads(o["l"])}
        legacy_ids = {e["entity_id"] for b in mine for e in _loads(b["e"]) if e.get("entity_id")} - other_ids
        legacy_triples = {_triple(l) for b in mine for l in _loads(b["l"])} - other_triples
        if legacy_ids:
            found = await _rows(
                session,
                "MATCH (e:Entity) WHERE e.entity_id IN $ids AND e.source_id IS NULL RETURN e.entity_id AS id",
                ids=sorted(legacy_ids),
            )
            legacy_found = {r["id"] for r in found} - owned_entities
            plan.legacy_entities = len(legacy_found)
            owned_entities |= legacy_found
        if legacy_triples:
            found = await _rows(
                session,
                """
                UNWIND $rows AS row
                MATCH (s:Entity {entity_id: row.src})-[r:LINK {link_id: row.lid}]->(t:Entity {entity_id: row.tgt})
                WHERE r.source_id IS NULL
                RETURN row.lid AS lid, row.src AS src, row.tgt AS tgt
                """,
                rows=[{"lid": l, "src": s, "tgt": t} for l, s, t in sorted(legacy_triples)],
            )
            legacy_found_links = {(r["lid"], r["src"], r["tgt"]) for r in found} - owned_links
            plan.legacy_links = len(legacy_found_links)
            owned_links |= legacy_found_links

    # ---- entities another source still depends on are kept ---------------------
    deleting: set[str] = set()
    if owned_entities:
        touching = await _rows(
            session,
            """
            UNWIND $ids AS id
            MATCH (e:Entity {entity_id: id})
            OPTIONAL MATCH (e)-[r:LINK]-()
            RETURN e.entity_id AS id,
                   collect(CASE WHEN r IS NULL THEN NULL
                           ELSE [r.link_id, startNode(r).entity_id, endNode(r).entity_id] END) AS links
            """,
            ids=sorted(owned_entities),
        )
        for row in touching:
            outside = [l for l in row["links"] if l is not None and tuple(l) not in owned_links]
            if outside:
                plan.kept_shared_entities.append(row["id"])
            else:
                deleting.add(row["id"])
    plan.entity_ids = sorted(deleting)
    plan.kept_shared_entities.sort()
    plan.links = sorted(owned_links)
    if plan.entity_ids:
        sample = await _rows(
            session,
            "MATCH (e:Entity) WHERE e.entity_id IN $ids RETURN e.label AS label ORDER BY e.entity_id LIMIT $n",
            ids=plan.entity_ids,
            n=_SAMPLE,
        )
        plan.entity_labels = [r["label"] for r in sample if r["label"]]

    # ---- ontology terms this source introduced ------------------------------------
    pending = await _rows(
        session,
        "MATCH (b:IngestBatch {status: 'proposed'}) WHERE coalesce(b.document_id, '') <> $id RETURN b.entities AS e, b.links AS l",
        id=sid,
    )
    pending_subclasses = {e.get("entity_subclass") for p in pending for e in _loads(p["e"])}
    pending_link_types = {l.get("link_type") for p in pending for l in _loads(p["l"])}

    owned_classes = await _rows(
        session, "MATCH (c:ClassDef {source_id: $sid}) RETURN c.key AS key, c.level AS level", sid=sid
    )
    removed_classes: set[str] = set()
    # Deepest first, so a parent is judged after the children of ours that go.
    for row in sorted(owned_classes, key=lambda r: (-(r["level"] or 0), r["key"])):
        key = row["key"]
        used = (await _rows(
            session,
            """
            MATCH (e:Entity)
            WHERE (e.entity_subclass = $key OR e.entity_subclass STARTS WITH $prefix)
              AND NOT e.entity_id IN $deleting
            RETURN count(e) AS n
            """,
            key=key,
            prefix=key + ".",
            deleting=plan.entity_ids,
        ))[0]["n"]
        children = {r["key"] for r in await _rows(session, "MATCH (c:ClassDef {parent_key: $key}) RETURN c.key AS key", key=key)}
        if used:
            plan.kept_classes.append({"key": key, "reason": f"{used} other entities use it"})
        elif any(s and (s == key or s.startswith(key + ".")) for s in pending_subclasses):
            plan.kept_classes.append({"key": key, "reason": "another pending import uses it"})
        elif children - removed_classes:
            plan.kept_classes.append({"key": key, "reason": "it has subclasses from elsewhere"})
        else:
            removed_classes.add(key)
            plan.classes.append(key)

    owned_types = await _rows(session, "MATCH (l:LinkDef {source_id: $sid}) RETURN l.type AS type ORDER BY l.type", sid=sid)
    for row in owned_types:
        t = row["type"]
        remaining = [
            r for r in await _rows(
                session,
                """
                MATCH (s:Entity)-[r:LINK {link_type: $t}]->(x:Entity)
                RETURN r.link_id AS lid, s.entity_id AS src, x.entity_id AS tgt
                """,
                t=t,
            )
            if (r["lid"], r["src"], r["tgt"]) not in owned_links
        ]
        if remaining:
            plan.kept_link_types.append({"type": t, "reason": f"{len(remaining)} other links use it"})
        elif t in pending_link_types:
            plan.kept_link_types.append({"type": t, "reason": "another pending import uses it"})
        else:
            plan.link_types.append(t)
    return plan


def remove_upload_dir(upload_dir: str, document_id: str) -> None:
    """Delete the document's stored file(s). Only ever removes a directory
    directly under upload_dir."""
    root = Path(upload_dir).resolve()
    target = (root / document_id).resolve()
    if target.parent == root:
        shutil.rmtree(target, ignore_errors=True)


async def delete_source(
    session: AsyncSession, doc: dict, *, remove_data: bool, upload_dir: str, now: datetime | None = None
) -> dict:
    """Delete the document; with remove_data also what it created. Returns a summary."""
    ensure_not_busy(doc, now)
    sid = doc["document_id"]
    result: dict = {"document_id": sid, "data_removed": remove_data}

    if remove_data:
        plan = await plan_removal(session, doc)
        if plan.links:
            await session.run(
                """
                UNWIND $rows AS row
                MATCH (:Entity {entity_id: row.src})-[r:LINK {link_id: row.lid}]->(:Entity {entity_id: row.tgt})
                DELETE r
                """,
                rows=[{"lid": l, "src": s, "tgt": t} for l, s, t in plan.links],
            )
        if plan.entity_ids:
            await session.run(
                "UNWIND $ids AS id MATCH (e:Entity {entity_id: id}) DETACH DELETE e", ids=plan.entity_ids
            )
        if plan.classes:
            await session.run("UNWIND $keys AS k MATCH (c:ClassDef {key: k}) DETACH DELETE c", keys=plan.classes)
        if plan.link_types:
            await session.run("UNWIND $types AS t MATCH (l:LinkDef {type: t}) DETACH DELETE l", types=plan.link_types)
        # Whatever survived (shared, or still in use) no longer belongs to a deleted source.
        await _untag(session, sid)
        result.update(plan.summary())
    else:
        await _untag(session, sid)

    await session.run("MATCH (b:IngestBatch {document_id: $id}) DETACH DELETE b", id=sid)
    await session.run("MATCH (d:Document {document_id: $id}) DETACH DELETE d", id=sid)
    remove_upload_dir(upload_dir, sid)
    return result


async def _untag(session: AsyncSession, sid: str) -> None:
    for query in (
        "MATCH (e:Entity {source_id: $sid}) REMOVE e.source_id",
        "MATCH ()-[r:LINK {source_id: $sid}]->() REMOVE r.source_id",
        "MATCH (c:ClassDef {source_id: $sid}) REMOVE c.source_id",
        "MATCH (l:LinkDef {source_id: $sid}) REMOVE l.source_id",
    ):
        await session.run(query, sid=sid)
