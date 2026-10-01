"""The finance extension must stay consistent with the base xlsx ontology:
real parents, no collisions, valid domain/range labels, unambiguous leaves.
"""
import sys
from pathlib import Path

import pytest

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
sys.path.insert(0, str(ONTOLOGY_DIR))

import finance_extension as fx  # noqa: E402
import import_ontology as base  # noqa: E402


@pytest.fixture(scope="module")
def wb():
    return base.load_workbook(base.DEFAULT_XLSX)


@pytest.fixture(scope="module")
def base_classes(wb):
    return {c["key"]: c for c in base.class_defs(wb)}


@pytest.fixture(scope="module")
def base_links(wb):
    return base.link_defs(wb)


def test_new_classes_do_not_collide_with_base(base_classes):
    assert not {c["key"] for c in fx.class_rows()} & set(base_classes)


def test_every_parent_exists_in_base_or_earlier_extension(base_classes):
    known = set(base_classes)
    for row in fx.class_rows():
        assert row["parent_key"] in known, row["key"]
        known.add(row["key"])


def test_levels_match_depth():
    for row in fx.class_rows():
        assert row["level"] == row["key"].count(".") + 1


def test_new_class_names_are_unambiguous_under_abbreviation(base_classes):
    # resolve_class_key accepts a trailing-segment abbreviation only if unique.
    all_keys = set(base_classes) | {c["key"] for c in fx.class_rows()}
    for row in fx.class_rows():
        last = row["key"].split(".")[-1]
        assert [k for k in all_keys if k.endswith("." + last)] == [row["key"]], last


def test_link_types_and_inverses_do_not_collide(base_links):
    taken = {l["type"] for l in base_links} | {l["inverse"] for l in base_links if l["inverse"]}
    new = [l["type"] for l in fx.LINKS] + [l["inverse"] for l in fx.LINKS]
    assert len(new) == len(set(new))
    assert not set(new) & taken


def test_domain_and_range_use_real_root_labels(base_classes):
    roots = {c["label"] for c in base_classes.values() if c["parent_key"] is None}
    for link in fx.link_rows():
        for side in (link["domain"], link["range"]):
            for part in side.split(";"):
                assert part.strip() in roots, (link["type"], part)


def test_link_rows_have_the_base_schema_fields(base_links):
    base_fields = set(base_links[0])
    for link in fx.link_rows():
        assert set(link) == base_fields
