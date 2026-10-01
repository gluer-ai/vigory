"""The sandbox assistant: operations are validated data, never executed code."""
import pytest

from app.models.sandbox import SandboxEdge, SandboxNode
from app.services import sandbox_agent as ag
from app.services.sandbox_agent import ChatTurn, SandboxOp, apply_ops, parse_ops, run_chat

CLASSES = {
    "PERSON": "person", "PERSON.MILITARY": "person",
    "ORGANIZATION": "organization", "ORGANIZATION.COMPANY": "organization",
    "LOCATION": "location", "LOCATION.CITY": "location",
}
LINKS = {
    "commands": {"domain": "Person", "range": "Organization"},
    "headquartered_in": {"domain": "Organization", "range": "Location"},
    "same_as": {"domain": "Any", "range": "Any"},
}


def node(id, label, sub, parent=None, x=0, y=0):
    return SandboxNode(id=id, origin="new", label=label, entity_subclass=sub, x=x, y=y, parent=parent)


def edge(id, s, t, type_, parent=None):
    return SandboxEdge(id=id, origin="new", source=s, target=t, link_type=type_, parent=parent)


def base():
    return (
        [node("P", "Ivan", "PERSON.MILITARY"), node("O", "Acme", "ORGANIZATION.COMPANY"), node("L", "Oslo", "LOCATION.CITY")],
        [edge("E1", "P", "O", "commands")],
    )


async def run(ops, container=None, nodes=None, edges=None):
    n, e = base()
    parsed, bad = parse_ops(ops)
    out_n, out_e, results = await apply_ops(None, nodes or n, edges or e, parsed, container, CLASSES, LINKS)
    return out_n, out_e, bad + results


def ids(items):
    return sorted(i.id for i in items)


async def test_rename_reclassify_and_case_insensitive_refs():
    nodes, _, res = await run([
        {"op": "rename_entity", "entity": "ivan", "label": "Ivan P."},
        {"op": "reclassify_entity", "entity": "Acme", "entity_subclass": "company"},  # abbreviation -> full key
    ])
    assert all(r["ok"] for r in res), res
    by = {n.id: n for n in nodes}
    assert by["P"].label == "Ivan P." and by["O"].entity_subclass == "ORGANIZATION.COMPANY"


async def test_add_entity_goes_on_the_current_level_or_a_named_container():
    nodes, _, res = await run([
        {"op": "add_entity", "label": "Inside Acme", "entity_subclass": "LOCATION.CITY", "inside": "Acme"},
        {"op": "add_entity", "label": "On screen", "entity_subclass": "LOCATION.CITY"},
        {"op": "add_entity", "label": "Back at top", "entity_subclass": "LOCATION.CITY", "inside": "top"},
    ], container="P")
    assert all(r["ok"] for r in res), res
    parent = {n.label: n.parent for n in nodes}
    assert parent["Inside Acme"] == "O" and parent["On screen"] == "P" and parent["Back at top"] is None
    new = [n for n in nodes if n.origin == "new" and n.label in parent and n.id not in {"P", "O", "L"}]
    assert all(n.id.startswith("N-") for n in new)


async def test_new_entities_do_not_stack_on_top_of_each_other():
    nodes, _, _ = await run([{"op": "add_entity", "label": f"X{i}", "entity_subclass": "LOCATION.CITY"} for i in range(6)])
    spots = {(n.x, n.y) for n in nodes if n.label.startswith("X")}
    assert len(spots) == 6


async def test_links_add_change_remove_and_valid_types_are_suggested():
    _, edges, res = await run([
        {"op": "add_link", "source": "Acme", "target": "Oslo", "link_type": "Headquartered In"},
        {"op": "change_link", "source": "Ivan", "target": "Acme", "link_type": "same_as"},
    ])
    assert all(r["ok"] for r in res), res
    assert {(e.source, e.target, e.link_type) for e in edges} == {("O", "L", "headquartered_in"), ("P", "O", "same_as")}

    nodes, edges, res = await run([{"op": "add_link", "source": "Ivan", "target": "Oslo", "link_type": "commands"}])
    assert not res[0]["ok"] and "Valid types: same_as" in res[0]["message"]  # tells the model what would work
    assert len(edges) == 1  # nothing was added

    _, edges, res = await run([{"op": "remove_link", "source": "Ivan", "target": "Acme"}])
    assert res[0]["ok"] and edges == []


async def test_removing_an_entity_cascades_to_links_and_nested_items():
    n, e = base()
    n.append(node("IN", "Inner", "LOCATION.CITY", parent="O"))
    out_n, out_e, res = await run([{"op": "remove_entity", "entity": "Acme"}], nodes=n, edges=e)
    assert res[0]["ok"] and "2 connected" in res[0]["message"]
    assert ids(out_n) == ["L", "P"] and out_e == []


async def test_a_failed_op_is_skipped_and_later_ops_still_run():
    nodes, _, res = await run([
        {"op": "rename_entity", "entity": "Nobody", "label": "x"},
        {"op": "reclassify_entity", "entity": "Ivan", "entity_subclass": "NOT.A.CLASS"},
        {"op": "rename_entity", "entity": "Ivan", "label": "Renamed"},
    ])
    assert [r["ok"] for r in res] == [False, False, True]
    assert "no entity named 'Nobody'" in res[0]["message"] and "not a known class" in res[1]["message"]
    assert {n.id: n.label for n in nodes}["P"] == "Renamed"


async def test_reclassifying_into_a_class_that_breaks_links_is_refused():
    nodes, edges, res = await run([{"op": "reclassify_entity", "entity": "Acme", "entity_subclass": "LOCATION.CITY"}])
    assert not res[0]["ok"] and "commands" in res[0]["message"]  # 'commands' needs an Organization target
    assert {n.id: n.entity_subclass for n in nodes}["O"] == "ORGANIZATION.COMPANY"


async def test_ambiguous_names_and_cross_level_links_are_rejected():
    n, e = base()
    n.append(node("O2", "Acme", "ORGANIZATION.COMPANY", parent="L"))
    _, _, res = await run([{"op": "rename_entity", "entity": "Acme", "label": "x"}], nodes=n, edges=e, container=None)
    assert res[0]["ok"]  # same-level match wins over the nested namesake
    _, _, res = await run([{"op": "rename_entity", "entity": "Acme", "label": "x"}], nodes=n, edges=e, container="IN-NOWHERE")
    assert not res[0]["ok"] and "matches 2" in res[0]["message"]  # no level preference -> must disambiguate
    _, _, res = await run([{"op": "add_link", "source": "Ivan", "target": "O2", "link_type": "commands"}], nodes=n, edges=e)
    assert not res[0]["ok"] and "different levels" in res[0]["message"]


async def test_unknown_and_malformed_ops_are_dropped_not_executed():
    ops, bad = parse_ops([
        {"op": "drop_database"}, {"op": "rename_entity", "entity": "x", "label": "y", "evil": "1"},
        "rm -rf /", {"op": "add_entity", "label": "z" * 500},
    ])
    assert [o.op for o in ops] == ["rename_entity"]
    assert len(bad) == 3 and all(not b["ok"] for b in bad)
    assert parse_ops("nonsense") == ([], [])


async def test_op_count_is_capped():
    ops = [SandboxOp(op="rename_entity", entity="P", label=f"n{i}") for i in range(ag.MAX_OPS + 3)]
    n, e = base()
    _, _, res = await apply_ops(None, n, e, ops, None, CLASSES, LINKS)
    assert sum(r["ok"] for r in res) == ag.MAX_OPS and "at most" in res[-1]["message"]


async def test_copy_from_graph_is_read_only_and_pulls_links_on_the_same_level(monkeypatch):
    real = {"entity_id": "R-1", "label": "Fjord Port", "entity_subclass": "LOCATION.CITY"}
    writes = []

    async def search(session, q, limit):
        return [real] if q.lower() == "fjord port" else []

    async def load(session, eid):
        return real if eid == "R-1" else None

    async def real_links(session, eid):
        return [
            {"link_id": "K1", "source_entity": "O", "target_entity": "R-1", "link_type": "headquartered_in"},
            {"link_id": "K2", "source_entity": "R-1", "target_entity": "FAR", "link_type": "same_as"},  # not on canvas
        ]

    monkeypatch.setattr(ag, "search_entities_by_name", search)
    monkeypatch.setattr(ag, "_load_graph_entity", load)
    monkeypatch.setattr(ag, "_load_graph_links", real_links)
    nodes, edges, res = await run([
        {"op": "copy_from_graph", "label": "Fjord Port"},
        {"op": "copy_from_graph", "entity_id": "R-1"},         # already there now
        {"op": "copy_from_graph", "label": "Nowhere Land"},
        {"op": "copy_from_graph", "entity_id": "R-404"},
    ])
    assert [r["ok"] for r in res] == [True, False, False, False]
    assert "with 1 existing link" in res[0]["message"]
    copied = next(n for n in nodes if n.id == "R-1")
    assert copied.origin == "graph" and copied.base.label == "Fjord Port"
    assert [e.id for e in edges if e.origin == "graph"] == ["K1"]
    assert writes == []


# ---- chat --------------------------------------------------------------------------


async def test_chat_applies_model_ops_and_returns_the_new_canvas(monkeypatch):
    seen = {}

    async def fake_llm(system, user):
        seen["system"], seen["user"] = system, user
        return {"reply": "Renamed Ivan.", "ops": [{"op": "rename_entity", "entity": "Ivan", "label": "Ivan Petrov"}]}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    n, e = base()
    out = await run_chat(None, "call Ivan 'Ivan Petrov'", [ChatTurn(role="user", text="hi")], n, e, None, CLASSES, LINKS)
    assert out["changed"] and out["reply"] == "Renamed Ivan."
    assert {x["id"]: x["label"] for x in out["nodes"]}["P"] == "Ivan Petrov"
    # the model is shown the canvas, the ontology and the history - and the ids it must use
    assert "P | Ivan | PERSON.MILITARY | top" in seen["user"] and "USER: hi" in seen["user"]
    assert "LOCATION.CITY" in seen["system"] and "commands (Person -> Organization)" in seen["system"]
    assert "PERSON\n" not in seen["system"].split("ENTITY_CLASSES:")[1].split("LINK_TYPES")[0]  # parents are not offered


async def test_chat_retries_once_with_the_failure_reason(monkeypatch):
    prompts = []

    async def fake_llm(system, user):
        prompts.append(user)
        if len(prompts) == 1:
            return {"reply": "Linked.", "ops": [
                {"op": "rename_entity", "entity": "Acme", "label": "Acme Corp"},
                {"op": "add_link", "source": "Ivan", "target": "Oslo", "link_type": "commands"},
            ]}
        return {"reply": "Linked them as the same entity.", "ops": [
            {"op": "add_link", "source": "Ivan", "target": "Oslo", "link_type": "same_as"}]}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    n, e = base()
    out = await run_chat(None, "link Ivan to Oslo", [], n, e, None, CLASSES, LINKS)
    assert len(prompts) == 2
    assert "cannot connect 'Ivan' to 'Oslo'" in prompts[1] and "Acme Corp" in prompts[1]  # sees the failure AND the applied rename
    assert [r["ok"] for r in out["results"]] == [True, True]  # the failure was replaced by its correction
    assert out["reply"] == "Linked them as the same entity."
    assert {(x["source"], x["target"], x["link_type"]) for x in out["edges"]} >= {("P", "L", "same_as")}


async def test_chat_keeps_first_pass_changes_if_the_retry_call_fails(monkeypatch):
    calls = []

    async def fake_llm(system, user):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("provider down")
        return {"reply": "ok", "ops": [
            {"op": "rename_entity", "entity": "Ivan", "label": "Kept"},
            {"op": "remove_entity", "entity": "Ghost"}]}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    n, e = base()
    out = await run_chat(None, "x", [], n, e, None, CLASSES, LINKS)
    assert {x["id"]: x["label"] for x in out["nodes"]}["P"] == "Kept"
    assert [r["ok"] for r in out["results"]] == [True, False]


async def test_chat_with_no_ops_just_replies(monkeypatch):
    async def fake_llm(system, user):
        return {"reply": "Which Acme do you mean?", "ops": []}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    n, e = base()
    out = await run_chat(None, "update acme", [], n, e, None, CLASSES, LINKS)
    assert out["changed"] is False and out["reply"] == "Which Acme do you mean?" and out["results"] == []


def test_rate_limit_is_per_user_and_recovers():
    ag._calls.clear()
    for _ in range(ag.CHAT_PER_MINUTE):
        ag.check_rate("u1", now=100)
    with pytest.raises(Exception) as e:
        ag.check_rate("u1", now=101)
    assert e.value.status_code == 429
    ag.check_rate("u2", now=101)
    ag.check_rate("u1", now=161)


async def test_reply_never_claims_success_for_failed_changes(monkeypatch):
    async def liar(system, user):  # claims success every time, even when shown the failure
        return {"reply": "Removed Ghost.", "ops": [{"op": "remove_entity", "entity": "Ghost"}]}

    monkeypatch.setattr(ag, "complete_json", liar)
    n, e = base()
    out = await run_chat(None, "remove ghost", [], n, e, None, CLASSES, LINKS)
    assert out["reply"] == "I couldn't make that change." and out["changed"] is False
    assert [r["ok"] for r in out["results"]] == [False]

    async def mixed(system, user):
        return {"reply": "Done.", "ops": [
            {"op": "rename_entity", "entity": "Ivan", "label": "Kept"},
            {"op": "remove_entity", "entity": "Ghost"}]}

    monkeypatch.setattr(ag, "complete_json", mixed)
    out = await run_chat(None, "x", [], n, e, None, CLASSES, LINKS)
    assert out["reply"].startswith("Done.") and "could not be applied" in out["reply"]


async def test_an_honest_explanation_after_the_retry_is_kept(monkeypatch):
    calls = []

    async def fake_llm(system, user):
        calls.append(1)
        if len(calls) == 1:
            return {"reply": "Removed.", "ops": [{"op": "remove_entity", "entity": "Ghost"}]}
        return {"reply": "There is no entity called Ghost to remove.", "ops": []}

    monkeypatch.setattr(ag, "complete_json", fake_llm)
    n, e = base()
    out = await run_chat(None, "remove ghost", [], n, e, None, CLASSES, LINKS)
    assert out["reply"] == "There is no entity called Ghost to remove."
    assert [r["ok"] for r in out["results"]] == [False]  # the failure is still listed
