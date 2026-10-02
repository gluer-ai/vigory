"""Exporting a sandbox for the real graph: fresh ids, no overwrites, no duplicates."""
from app.services.sandbox_export import plan_export

PER, ORG = "PERSON.MILITARY_PERSONNEL", "ORGANIZATION.COMMERCIAL_ENTITY"


def n(id, label, origin="new", sub=PER, parent=None, base=True):
    return {"id": id, "origin": origin, "label": label, "entity_subclass": sub, "x": 0, "y": 0, "parent": parent,
            "base": {"label": label, "entity_subclass": sub} if origin == "graph" and base else None}


def e(id, s, t, type_="commands", origin="new", parent=None, base_type=None):
    return {"id": id, "origin": origin, "source": s, "target": t, "link_type": type_, "parent": parent,
            "base_type": base_type if origin == "new" else (base_type or type_)}


def plan(nodes, edges, taken=None):
    return plan_export(nodes, edges, "My what-if", set(taken or []))


def test_new_entities_and_links_get_fresh_ids_never_the_sandbox_ones():
    p = plan([n("N-aaaa", "Ivan"), n("N-bbbb", "Acme", sub=ORG)], [e("E-1", "N-aaaa", "N-bbbb")])
    assert [x["label"] for x in p.entities] == ["Ivan", "Acme"]
    ids = {x["entity_id"] for x in p.entities}
    assert not ids & {"N-aaaa", "N-bbbb"} and all(i.startswith("E-") for i in ids)
    link = p.links[0]
    assert link["link_id"] not in {"E-1"} and {link["source_entity"], link["target_entity"]} == ids
    assert p.entities[0]["source_ref"] == "Sandbox: My what-if" and p.entities[0]["entity_class"] == "PERSON"


def test_a_sandbox_id_that_equals_a_real_entity_id_cannot_overwrite_it():
    # a new node deliberately named like a real record ("P-temp2", the real Lehman)
    p = plan([n("P-temp2", "Impostor")], [], taken={"P-temp2"})
    assert p.entities[0]["entity_id"] != "P-temp2"


def test_fresh_ids_avoid_every_id_already_in_the_graph(monkeypatch):
    import app.services.sandbox_export as mod
    seq = iter(["000000000001", "000000000002", "000000000003"])
    monkeypatch.setattr(mod.uuid, "uuid4", lambda: type("U", (), {"hex": next(seq) + "f" * 20})())
    p = plan([n("N-1", "A")], [], taken={"E-000000000001"})  # first candidate is taken
    assert p.entities[0]["entity_id"] == "E-000000000002"


def test_real_entities_are_never_proposed_and_edits_to_them_are_reported():
    nodes = [n("R-1", "Renamed", origin="graph", base=False), n("R-2", "Same", origin="graph")]
    nodes[0]["base"] = {"label": "Original", "entity_subclass": PER}
    p = plan(nodes, [e("K1", "R-1", "R-2", origin="graph", base_type="owns")])
    assert p.entities == [] and p.links == []
    assert p.skipped["edits to existing entities (real records are never changed)"] == 1
    assert p.skipped["changes to existing links (real records are never changed)"] == 1


def test_a_new_link_between_a_new_and_a_real_entity_uses_the_real_id():
    p = plan([n("N-1", "Ivan"), n("R-9", "Acme", origin="graph", sub=ORG)], [e("E-1", "N-1", "R-9")])
    assert p.links[0]["target_entity"] == "R-9" and p.links[0]["source_entity"] == p.entities[0]["entity_id"]


def test_same_as_a_real_entity_merges_instead_of_duplicating():
    nodes = [n("N-leh", "Lehman Brothers Holdings Inc.", sub=ORG), n("R-leh", "Lehman Brothers", origin="graph", sub=ORG),
             n("N-fuld", "Richard Fuld"), n("N-nyc", "New York City", sub="LOCATION.CITY")]
    edges = [e("E-same", "N-leh", "R-leh", "same_as"), e("E-1", "N-fuld", "N-leh", "commands"),
             e("E-2", "N-leh", "N-nyc", "headquartered_in")]
    p = plan(nodes, edges)
    assert [x["label"] for x in p.entities] == ["Richard Fuld", "New York City"]  # no duplicate Lehman
    assert p.merged == [{"label": "Lehman Brothers Holdings Inc.", "into_id": "R-leh", "into_label": "Lehman Brothers"}]
    by = {(l["link_type"]): l for l in p.links}
    assert set(by) == {"commands", "headquartered_in"}  # the same_as link itself is not exported
    assert by["commands"]["target_entity"] == "R-leh" and by["headquartered_in"]["source_entity"] == "R-leh"


def test_ambiguous_same_as_is_not_merged():
    nodes = [n("N-1", "X"), n("R-1", "A", origin="graph"), n("R-2", "B", origin="graph")]
    p = plan(nodes, [e("S1", "N-1", "R-1", "same_as"), e("S2", "N-1", "R-2", "same_as")])
    assert p.merged == [] and len(p.entities) == 1 and len(p.links) == 2
    assert "kept as a new entity" in p.notes[0]


def test_nested_content_is_not_exported_and_is_counted():
    nodes = [n("N-1", "Top"), n("N-in", "Inside", parent="N-1")]
    p = plan(nodes, [e("E-in", "N-in", "N-in2", parent="N-1")])
    assert [x["label"] for x in p.entities] == ["Top"]
    assert p.skipped["inside opened entities/links (not exported)"] == 2


def test_dangling_self_and_duplicate_links_are_dropped():
    nodes = [n("N-1", "A"), n("N-2", "B")]
    edges = [e("E-1", "N-1", "N-2"), e("E-2", "N-1", "N-2"), e("E-3", "N-1", "GONE"), e("E-4", "N-1", "N-1")]
    p = plan(nodes, edges)
    assert len(p.links) == 1
    assert p.skipped["duplicate links"] == 1 and p.skipped["links to something that is not on the top level"] == 1


def test_a_vanished_real_entity_is_not_a_merge_target_and_links_to_it_are_dropped():
    nodes = [n("N-a", "Anna"), n("R-gone", "Deleted since", origin="graph"), n("R-ok", "Acme", origin="graph", sub=ORG)]
    edges = [e("S", "N-a", "R-gone", "same_as"), e("E-1", "N-a", "R-ok"), e("E-2", "R-gone", "R-ok")]
    p = plan_export(nodes, edges, "x", set(), real_ids={"R-ok"})
    assert [x["label"] for x in p.entities] == ["Anna"] and p.merged == []  # Anna is kept, not absorbed
    assert [(l["source_entity"] == p.entities[0]["entity_id"], l["target_entity"]) for l in p.links] == [(True, "R-ok")]
    assert p.skipped["links to entities that are no longer in the knowledge graph"] == 2


def test_an_entity_saved_earlier_is_reused_not_created_again():
    nodes = [n("N-1", "Fuld"), n("N-2", "Acme", sub=ORG)]
    edges = [e("E-1", "N-1", "N-2")]
    first = plan_export(nodes, edges, "x", set())
    assert first.id_map == {"N-1": first.entities[0]["entity_id"], "N-2": first.entities[1]["entity_id"]}
    again = plan_export(nodes, edges, "x", set(), real_ids=set(first.id_map.values()), saved=first.id_map)
    assert again.entities == []  # nothing is created twice
    assert again.skipped["entities already saved to the graph from this sandbox"] == 2
    # its link is still proposed, between the saved records; the existing-link check in the
    # database layer then drops it as already present
    assert (again.links[0]["source_entity"], again.links[0]["target_entity"]) == (first.id_map["N-1"], first.id_map["N-2"])


def test_a_saved_entity_that_was_later_deleted_from_the_graph_is_created_again():
    nodes = [n("N-1", "Fuld")]
    p = plan_export(nodes, [], "x", set(), real_ids=set(), saved={"N-1": "E-gone"})
    assert [x["label"] for x in p.entities] == ["Fuld"] and p.entities[0]["entity_id"] != "E-gone"
