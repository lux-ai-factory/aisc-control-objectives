"""Objective sets and objective profiles, on a project's database.

A set is where objectives are written: the user picks its code, its objectives are code + number,
never reused, and it is published as numbered, immutable versions. A profile is picked from the
built-in set and from sets' published versions; each save is a new version pinned to the set
versions it took, and it says when a newer one is out. The built-in set and the Full AI Act profile
are the packaged CSV and are not stored.
"""
from __future__ import annotations

import pytest

from aisc_control_objectives.library import FULL_AI_ACT, Library

ALICE = "alice"


def fields(**over):
    base = {"dimension": "R2", "label": "Model change approval", "text": "Every model change is approved before use.",
                "legal_basis": "Internal policy 7", "assessment_mode": "Control", "target": "G",
                "standards_grounding": "", "grounding_tier_flag": "", "notes": ""}
    base.update(over)
    return base


@pytest.fixture
def library(repository, objectives):
    return Library(repository.engine, objectives)


@pytest.fixture
def bnk(library):
    made = library.create_set("BNK", "Bank policies", "Our own controls", who=ALICE)
    library.add_objective(made.id, fields())
    library.add_objective(made.id, fields(dimension="R5", label="Fair pricing", text="Prices are reviewed for bias."))
    return made


# Sets

def test_the_built_in_set_is_listed_first_and_read_only(library, bnk):
    sets = library.sets()
    assert [(s.code, s.read_only) for s in sets] == [("O", True), ("BNK", False)]
    assert sets[0].objectives == 50


@pytest.mark.parametrize("code", ["bnk", "B", "O", "TOOLONG", "BN1", "B K", ""])
def test_a_code_is_two_to_six_capital_letters(library, code):
    with pytest.raises(ValueError, match="code"):
        library.create_set(code, "x", "", who=ALICE)


def test_a_code_is_unique(library, bnk):
    with pytest.raises(ValueError, match="BNK"):
        library.create_set("BNK", "again", "", who=ALICE)


def test_objectives_are_numbered_in_their_set(library, bnk):
    draft = library.get_set(bnk.id).draft
    assert [(o.id, o.macro_id, o.sub_requirement_label) for o in draft] == [
        ("BNK1", "R2", "Model change approval"), ("BNK2", "R5", "Fair pricing")]


@pytest.mark.parametrize("bad, why", [
    ({"dimension": "R12"}, "dimension"), ({"dimension": ""}, "dimension"),
    ({"label": " "}, "label"), ({"text": ""}, "text"), ({"assessment_mode": "Maybe"}, "mode"),
])
def test_an_objective_needs_a_dimension_a_label_a_text_and_a_mode(library, bnk, bad, why):
    with pytest.raises(ValueError, match=why):
        library.add_objective(bnk.id, fields(**bad))


def test_a_retired_number_is_never_given_again(library, bnk):
    library.retire(bnk.id, "BNK2")
    assert library.add_objective(bnk.id, fields(label="Third")) == "BNK3"
    view = library.get_set(bnk.id)
    assert [(o.id, retired) for o, retired in view.draft_rows] == [("BNK1", False), ("BNK2", True), ("BNK3", False)]
    library.restore(bnk.id, "BNK2")
    assert [retired for _, retired in library.get_set(bnk.id).draft_rows] == [False, False, False]


def test_publishing_makes_immutable_numbered_versions(library, bnk):
    assert library.publish(bnk.id, who=ALICE) == 1
    with pytest.raises(ValueError, match="nothing changed"):
        library.publish(bnk.id, who=ALICE)
    library.edit_objective(bnk.id, "BNK1", fields(label="Model change sign-off"))
    library.retire(bnk.id, "BNK2")
    assert library.publish(bnk.id, who=ALICE) == 2
    v1, v2 = library.set_version(bnk.id, 1), library.set_version(bnk.id, 2)
    assert [(o.id, o.sub_requirement_label) for o in v1] == [("BNK1", "Model change approval"), ("BNK2", "Fair pricing")]
    assert [(o.id, o.sub_requirement_label) for o in v2] == [("BNK1", "Model change sign-off")]


def test_a_set_with_nothing_active_cannot_be_published(library):
    empty = library.create_set("EMP", "Empty", "", who=ALICE)
    with pytest.raises(ValueError, match="no objective"):
        library.publish(empty.id, who=ALICE)


def test_only_a_set_never_published_can_be_deleted(library, bnk):
    other = library.create_set("TMP", "Scratch", "", who=ALICE)
    library.delete_set(other.id)
    assert [s.code for s in library.sets()] == ["O", "BNK"]
    library.publish(bnk.id, who=ALICE)
    with pytest.raises(ValueError, match="published"):
        library.delete_set(bnk.id)


def test_only_published_objectives_can_be_picked(library, bnk):
    assert "BNK1" not in {o.id for o in library.available()}
    library.publish(bnk.id, who=ALICE)
    library.add_objective(bnk.id, fields(label="Draft only"))
    available = [o.id for o in library.available()]
    assert available[:50] == [f"O{n}" for n in range(1, 51)] and available[50:] == ["BNK1", "BNK2"]


# Profiles

def test_the_full_ai_act_profile_is_built_in(library):
    profiles = library.profiles()
    assert profiles[0].id == FULL_AI_ACT and profiles[0].read_only and profiles[0].objectives == 50
    assert [o.id for o in library.catalogue_of(None)] == [f"O{n}" for n in range(1, 51)]


def test_a_profile_picks_from_the_built_in_set_and_published_sets(library, bnk):
    library.publish(bnk.id, who=ALICE)
    profile = library.create_profile("Bank", "", ["BNK1", "O7", "O1"], who=ALICE)
    view = library.get_profile(profile.id)
    assert view.current.number == 1
    catalogue = library.catalogue_of(view.current.id)
    assert [o.id for o in catalogue] == ["O1", "O7", "BNK1"]
    assert catalogue.by_id("BNK1").macro_id == "R2" and catalogue.by_id("BNK1").regimes == ["other"]
    assert view.pins == {"BNK": 1}


@pytest.mark.parametrize("picks, why", [([], "at least one"), (["O1", "BNK9"], "BNK9"), (["O99"], "O99")])
def test_a_profile_takes_only_objectives_that_can_be_picked(library, bnk, picks, why):
    library.publish(bnk.id, who=ALICE)
    with pytest.raises(ValueError, match=why):
        library.create_profile("Bad", "", picks, who=ALICE)


def test_a_newer_set_version_is_offered_and_taken_only_on_saving(library, bnk):
    library.publish(bnk.id, who=ALICE)
    profile = library.create_profile("Bank", "", ["O1", "BNK1", "BNK2"], who=ALICE)
    first = library.get_profile(profile.id).current
    library.edit_objective(bnk.id, "BNK1", fields(label="Model change sign-off"))
    library.retire(bnk.id, "BNK2")
    library.publish(bnk.id, who=ALICE)
    view = library.get_profile(profile.id)
    assert view.updates == {"BNK": (1, 2)}
    # the pinned version keeps its wording until the update is taken
    assert library.catalogue_of(first.id).by_id("BNK1").sub_requirement_label == "Model change approval"
    saved, dropped = library.save_profile(profile.id, ["O1", "BNK1", "BNK2"], who=ALICE)
    assert dropped == ["BNK2"]
    view = library.get_profile(profile.id)
    assert view.current.number == 2 and view.updates == {} and view.pins == {"BNK": 2}
    assert [o.id for o in library.catalogue_of(view.current.id)] == ["O1", "BNK1"]
    assert library.catalogue_of(view.current.id).by_id("BNK1").sub_requirement_label == "Model change sign-off"
    assert [o.id for o in library.catalogue_of(first.id)] == ["O1", "BNK1", "BNK2"]


def test_a_profile_is_saved_on_one_connection(repository, objectives):
    """Saving a profile version read what may be picked on a second connection while its transaction
    held the first (code review 2026-10-05). A project engine has two, so two saves at once waited
    on each other for pool_timeout and failed. On a pool of one, a save must still go through."""
    from sqlalchemy import create_engine

    one = create_engine(repository.engine.url, pool_size=1, max_overflow=0, pool_timeout=2)
    try:
        library = Library(one, objectives)
        made = library.create_profile("One connection", "", ["O1"], who=ALICE)
        version, _ = library.save_profile(made.id, ["O1", "O7"], who=ALICE)
        assert version.number == 2
    finally:
        one.dispose()
