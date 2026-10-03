"""Objective ids are O1 ... O50: one number each, in catalogue order.

The earlier ids were R1.1 ... R11.4, the macro-requirement in the id. The macro-requirements (R1 ... R11)
keep their ids, they are the trustworthiness dimensions; an objective's dimension is read from
its Macro_Requirement column, never from its id. `objective_id_renames.csv` is the table every
stored id was migrated with.
"""
from __future__ import annotations

import csv

import pytest

from aisc_control_objectives.control_objectives import default_csv_path, load_control_objectives
from aisc_control_objectives.models.control_objective import ControlObjective

RENAMES = default_csv_path().parent / "objective_id_renames.csv"


@pytest.fixture(scope="module")
def catalogue():
    return load_control_objectives()


def test_the_ids_are_o1_to_o50_in_catalogue_order(catalogue):
    assert [o.id for o in catalogue.objectives] == [f"O{n}" for n in range(1, 51)]


def test_o9_sorts_before_o10(catalogue):
    assert catalogue.by_id("O9").sort_key < catalogue.by_id("O10").sort_key


def test_the_dimension_comes_from_the_macro_requirement_column(catalogue):
    first, last = catalogue.by_id("O1"), catalogue.by_id("O50")
    assert (first.macro_id, first.macro_title) == ("R1", "Human Agency and Oversight")
    assert last.macro_id == "R11"
    assert [m.id for m in catalogue.macro_requirements()] == [f"R{n}" for n in range(1, 12)]


def test_an_old_id_is_not_an_objective(catalogue):
    assert catalogue.by_id("R1.1") is None
    with pytest.raises(ValueError):
        ControlObjective(id="R1.1", macro_requirement="R1 Human Agency and Oversight",
                         legal_basis="AI Act Art. 14", sub_requirement_label="x", text="x",
                         assessment_mode="Control", target="G", standards_grounding="",
                         grounding_tier_flag="Tier 3")


def test_the_rename_table_maps_every_old_id_once_in_order(catalogue):
    rows = list(csv.DictReader(RENAMES.open(encoding="utf-8")))
    assert [r["new_id"] for r in rows] == [o.id for o in catalogue.objectives]
    olds = [r["old_id"] for r in rows]
    assert len(set(olds)) == 50 and olds[0] == "R1.1" and olds[-1] == "R11.4"
    # each objective stayed under the macro-requirement its old id named
    for r in rows:
        assert catalogue.by_id(r["new_id"]).macro_id == r["old_id"].split(".")[0], r
