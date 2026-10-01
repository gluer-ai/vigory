"""Sandbox rules enforced on every save: ontology fit, size limits, nesting."""
import pytest

from app.models.sandbox import SandboxEdge, SandboxNode
from app.services import sandbox as svc
from app.services.sandbox import SandboxInvalid, validate_state

CLASSES = {
    "PERSON.A": "person",
    "ORGANIZATION.B": "organization",
    "LOCATION.C": "location",
}
LINKS = {
    "commands": {"domain": "Person", "range": "Organization"},
    "headquartered_in": {"domain": "Organization", "range": "Location; Facility"},
    "related_to": {"domain": "Any", "range": "Any"},
}


def n(id, sub="PERSON.A", parent=None):
    return SandboxNode(id=id, origin="new", label=id, entity_subclass=sub, x=0, y=0, parent=parent)


def e(id, s, t, type_="related_to", parent=None):
    return SandboxEdge(id=id, origin="new", source=s, target=t, link_type=type_, parent=parent)


def check(nodes, edges):
    validate_state(nodes, edges, CLASSES, LINKS)


def rejects(nodes, edges, match):
    with pytest.raises(SandboxInvalid, match=match):
        check(nodes, edges)


# ---- ontology / basics -----------------------------------------------------------


def test_valid_flat_canvas_passes():
    check([n("P"), n("O", "ORGANIZATION.B")], [e("E1", "P", "O", "commands")])


def test_rejects_unknown_class_link_type_and_bad_domain_range():
    rejects([n("P", "NOPE.X")], [], "not a known class")
    rejects([n("P"), n("O", "ORGANIZATION.B")], [e("E", "P", "O", "teleports")], "not a known link type")
    rejects([n("P"), n("L", "LOCATION.C")], [e("E", "P", "L", "commands")], "cannot use a location")


def test_rejects_duplicates_dangling_and_self_links():
    rejects([n("P"), n("P")], [], "Duplicate entity")
    rejects([n("P"), n("O", "ORGANIZATION.B")], [e("E", "P", "O"), e("E", "O", "P")], "Duplicate link")
    rejects([n("P")], [e("E", "P", "GONE")], "not in the sandbox")
    rejects([n("P")], [e("E", "P", "P")], "itself")
    rejects([n("X")], [e("X", "X", "X")], "share the same id|itself")


def test_size_limits(monkeypatch):
    monkeypatch.setattr(svc, "MAX_NODES", 2)
    rejects([n("A"), n("B"), n("C")], [], "at most 2 entities")
    monkeypatch.setattr(svc, "MAX_EDGES", 1)
    rejects([n("A"), n("B")], [e("1", "A", "B"), e("2", "B", "A")], "at most 1 links")


# ---- nesting ---------------------------------------------------------------------


def test_items_can_live_inside_an_entity_or_a_relation():
    nodes = [n("P"), n("O", "ORGANIZATION.B"), n("in-edge", parent="E1"), n("in-node", parent="P"), n("in-node2", parent="P")]
    edges = [e("E1", "P", "O", "commands"), e("E2", "in-node", "in-node2", parent="P")]
    check(nodes, edges)


def test_parent_must_exist():
    rejects([n("A", parent="GHOST")], [], "not in the sandbox")
    rejects([n("A"), n("B")], [e("E", "A", "B", parent="GHOST")], "not in the sandbox")


def test_no_item_contains_itself_or_forms_a_cycle():
    rejects([n("A", parent="A")], [], "contain themselves")
    rejects([n("A", parent="B"), n("B", parent="A")], [], "contain themselves")
    rejects([n("A", parent="B"), n("B", parent="C"), n("C", parent="A")], [], "contain themselves")


def test_nesting_depth_is_bounded(monkeypatch):
    chain = [n("N0")] + [n(f"N{i}", parent=f"N{i - 1}") for i in range(1, svc.MAX_DEPTH + 1)]
    check(chain, [])  # exactly MAX_DEPTH levels is fine
    chain.append(n("TOO-DEEP", parent=f"N{svc.MAX_DEPTH}"))
    rejects(chain, [], "limited to")


def test_a_link_must_join_entities_on_its_own_level():
    nodes = [n("P"), n("O", "ORGANIZATION.B"), n("inner", parent="P")]
    rejects(nodes, [e("E", "inner", "O", "commands")], "same level")  # crosses levels
    rejects(nodes, [e("E", "P", "O", "commands", parent="P")], "same level")  # link inside P, ends outside
    # an entity can't be an end of the very relation it sits inside
    rejects(
        [n("P"), n("O", "ORGANIZATION.B"), n("X", parent="E1")],
        [e("E1", "P", "O", "commands"), e("E2", "X", "P", parent="E1")],
        "same level",
    )


def test_ontology_rules_still_apply_inside_a_container():
    nodes = [n("box"), n("p1", parent="box"), n("l1", "LOCATION.C", parent="box")]
    rejects(nodes, [e("E", "p1", "l1", "commands", parent="box")], "cannot use a location")
    check(nodes, [e("E", "p1", "l1", "related_to", parent="box")])
