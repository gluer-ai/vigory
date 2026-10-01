"""Ontology growth by the extraction agent: every model proposal is
validated before anything is written."""
import pytest

from app.services import ontology_extension as ox

KEYS = {
    "EVENT", "EVENT.TRANSACTION", "ORGANIZATION", "ORGANIZATION.COMMERCIAL_ENTITY",
    "PERSON", "LOCATION",
}
ROOTS = {"event": "Event", "organization": "Organization", "person": "Person", "location": "Location"}


# ---- names ----------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("BANKRUPTCY_FILING", "BANKRUPTCY_FILING"), ("bankruptcy filing", "BANKRUPTCY_FILING"),
    ("Bank-Run", "BANK_RUN"), ("", None), ("A.B", None), ("X" * 60, None), (None, None), (5, None),
    ("drop;table", None),
])
def test_normalize_class_name(raw, expected):
    assert ox.normalize_class_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("acquired", "acquired"), ("Headquartered In", "headquartered_in"),
    ("headquartered_in (Company -> Place)", "headquartered_in"),
    ("notified_by (Company -> Company)", "notified_by"), ("", None), ("a.b", None), (None, None),
])
def test_normalize_link_type(raw, expected):
    assert ox.normalize_link_type(raw) == expected


# ---- validators -------------------------------------------------------------


def test_new_class_requires_existing_parent_and_fresh_key():
    ok = ox.validate_new_class({"parent_key": "EVENT.TRANSACTION", "name": "bankruptcy filing", "notes": " n "}, KEYS)
    assert ok["key"] == "EVENT.TRANSACTION.BANKRUPTCY_FILING"
    assert ok["parent_key"] == "EVENT.TRANSACTION" and ok["level"] == 3
    assert ok["origin"] == "extraction" and ok["notes"] == "n"
    assert ox.validate_new_class({"parent_key": "NOPE", "name": "X"}, KEYS) is None
    assert ox.validate_new_class({"parent_key": "EVENT", "name": "TRANSACTION"}, KEYS) is None  # exists
    assert ox.validate_new_class({"parent_key": "EVENT", "name": "a.b"}, KEYS) is None
    assert ox.validate_new_class("junk", KEYS) is None
    deep = "A.B.C.D.E"
    assert ox.validate_new_class({"parent_key": deep, "name": "F"}, KEYS | {deep}) is None  # depth cap


def test_new_link_validates_roots_and_collisions():
    taken = {"member_of", "has_member"}
    ok = ox.validate_new_link(
        {"type": "Acquired", "domain": "organization; PERSON", "range": "Organization", "inverse": "acquired_by"},
        taken, ROOTS)
    assert ok["type"] == "acquired" and ok["domain"] == "Organization; Person"
    assert ok["inverse"] == "acquired_by" and ok["origin"] == "extraction"
    bad = [
        {"type": "member_of", "domain": "Person", "range": "Organization"},   # exists
        {"type": "has_member", "domain": "Person", "range": "Organization"},  # collides with an inverse
        {"type": "x", "domain": "Spaceship", "range": "Organization"},        # unknown root
        {"type": "x", "domain": "", "range": "Organization"},
        {"type": "bad.type", "domain": "Person", "range": "Person"},
    ]
    for item in bad:
        assert ox.validate_new_link(item, taken, ROOTS) is None
    clash = ox.validate_new_link({"type": "x", "domain": "Any", "range": "Any", "inverse": "member_of"}, taken, ROOTS)
    assert clash["domain"] == "Any" and clash["inverse"] is None  # clashing inverse dropped, link kept


# ---- end to end against a stateful fake graph -----------------------------


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def gen():
            for r in self.rows:
                yield r
        return gen()


class _Graph:
    def __init__(self):
        self.classes = {k: (k.rsplit(".", 1)[0] if "." in k else None) for k in KEYS}
        self.links = [{"type": "member_of", "domain": "Person", "range": "Organization", "inverse": "has_member"}]
        self.written_classes: list[dict] = []
        self.written_links: list[dict] = []

    async def run(self, query, **p):
        if "RETURN c.key AS key, c.parent_key AS parent" in query:
            return _Rows([{"key": k, "parent": v} for k, v in self.classes.items()])
        if "RETURN l.type AS type" in query:
            return _Rows(self.links)
        if "MERGE (c:ClassDef {key: row.key})" in query:
            self.written_classes += p["rows"]
            for r in p["rows"]:
                self.classes[r["key"]] = r["parent_key"]
            return _Rows([])
        if "MERGE (l:LinkDef" in query:
            self.written_links += p["rows"]
            self.links += p["rows"]
            return _Rows([])
        return _Rows([])  # SUBCLASS_OF edges


REJ_ENT = [{"row": {"label": "Lehman bankruptcy", "entity_class": "Event", "entity_subclass": "Event.Bankruptcy"}, "reason": "unknown"}]
REJ_LNK = [{"row": {"link_type": "acquired", "source_entity": "A", "target_entity": "B"}, "reason": "unknown"}]


async def test_extend_ontology_creates_valid_terms_and_returns_assignments(monkeypatch):
    async def fake(prompt, user):
        assert "Event.Bankruptcy" in prompt and "ENTITY_CLASSES" in prompt
        return {
            "new_classes": [
                {"parent_key": "EVENT.TRANSACTION", "name": "BANKRUPTCY_FILING"},
                {"parent_key": "MADE.UP", "name": "NOPE"},  # rejected
            ],
            "new_link_types": [{"type": "acquired", "domain": "Organization", "range": "Organization", "inverse": "acquired_by"}],
            "entity_assignments": [
                {"idx": 0, "entity_subclass": "EVENT.TRANSACTION.BANKRUPTCY_FILING"},
                {"idx": 7, "entity_subclass": "EVENT"},            # out of range
                {"idx": 0, "entity_subclass": "NOT.A.KEY"},        # unknown key ignored
            ],
            "link_assignments": [{"idx": 0, "link_type": "acquired (Organization -> Organization)"}],
        }

    monkeypatch.setattr(ox, "complete_json", fake)
    g = _Graph()
    out = await ox.extend_ontology(g, REJ_ENT, REJ_LNK, {"A": "Lehman (ORGANIZATION)", "B": "Barclays (ORGANIZATION)"})
    assert out["added_classes"] == ["EVENT.TRANSACTION.BANKRUPTCY_FILING"]
    assert out["added_links"] == ["acquired"]
    assert out["entity_assignments"] == {0: "EVENT.TRANSACTION.BANKRUPTCY_FILING"}
    assert out["link_assignments"] == {0: "acquired"}
    assert [c["origin"] for c in g.written_classes] == ["extraction"]
    assert g.written_links[0]["category"] == "Extracted"


async def test_extend_ontology_caps_additions_and_tolerates_garbage(monkeypatch):
    async def fake(prompt, user):
        return {
            "new_classes": [{"parent_key": "EVENT", "name": f"KIND_{i}"} for i in range(40)] + ["junk", None],
            "new_link_types": "not a list",
            "entity_assignments": ["junk", {"idx": "0"}, {"idx": 0, "entity_subclass": None}],
            "link_assignments": None,
        }

    monkeypatch.setattr(ox, "complete_json", fake)
    g = _Graph()
    out = await ox.extend_ontology(g, REJ_ENT, [])
    assert len(out["added_classes"]) == ox.MAX_NEW_CLASSES
    assert out["entity_assignments"] == {} and out["link_assignments"] == {}
