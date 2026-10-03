"""Which control objectives matter most: the scores behind the risk and control matrix.

Each risk is rated impact (1-5) x likelihood (1-5), 1 to 25, in the 5x5 bands risk teams know: Low
1-4, Medium 5-9, High 10-16, Critical 17-25. An unrated impact or likelihood counts 3. An objective
scores the sum of the ratings of the risks mapped to it; it is ranked by score, then its highest
single rating, then a binding duty first, then catalogue order. The **key** objectives are, by
default, the first seven driven by at least one High or Critical risk; the assessor can turn key on
or off for any objective, and that choice wins.
"""

from __future__ import annotations

import pytest

from aisc_control_objectives.models.ontology import OntologyRisk
from aisc_control_objectives.prioritising import KEY_BUDGET, Severity, band, prioritise
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping

RISKS = [OntologyRisk(id=f"r{n}", text=f"risk {n}") for n in range(1, 5)]


def maps(risk_id, *objective_ids):
    return Mapping(risk_id=risk_id, objectives=[MappedObjective(objective_id=o, quote="q") for o in objective_ids])


def rated(**pairs):
    """r1=(5, 4) -> impact 5, likelihood 4."""
    return Severity(impact={r: p[0] for r, p in pairs.items() if p[0]},
                    likelihood={r: p[1] for r, p in pairs.items() if p[1]})


def by_id(objectives, severity, mappings, **kw):
    return {p.objective_id: p for p in prioritise(objectives, severity, mappings, RISKS, **kw)}


# The rating

def test_a_rating_is_impact_times_likelihood():
    assert rated(r1=(5, 4)).of("r1") == 20


def test_an_unrated_impact_or_likelihood_counts_three():
    assert Severity().of("r1") == 9
    assert rated(r1=(5, None)).of("r1") == 15


@pytest.mark.parametrize("field", ["impact", "likelihood"])
@pytest.mark.parametrize("value", [0, 6])
def test_each_is_one_to_five(field, value):
    with pytest.raises(ValueError, match="1-5"):
        Severity(**{field: {"r1": value}})


@pytest.mark.parametrize("rating, name", [(1, "Low"), (4, "Low"), (5, "Medium"), (9, "Medium"),
                                          (10, "High"), (16, "High"), (20, "Critical"), (25, "Critical")])
def test_the_bands(rating, name):
    assert band(rating) == name


# The score and the rank

def test_an_objective_scores_the_sum_of_its_risks_ratings(objectives):
    got = by_id(objectives, rated(r1=(5, 4), r2=(2, 2)), {"r1": maps("r1", "O5"), "r2": maps("r2", "O5")})
    assert got["O5"].score == 24 and got["O5"].top_rating == 20
    assert got["O6"].score == 0 and got["O6"].top_rating == 0


def test_rank_is_score_then_top_rating_then_binding_then_catalogue_order(objectives):
    # O11: 12 + 4 = 16, top 12; O5: 8 + 8 = 16, top 8 -> O11 first
    got = by_id(objectives, rated(r1=(4, 3), r2=(2, 2), r3=(4, 2), r4=(2, 4)),
                {"r1": maps("r1", "O11"), "r2": maps("r2", "O11"), "r3": maps("r3", "O5"), "r4": maps("r4", "O5")})
    assert got["O11"].score == got["O5"].score == 16
    assert got["O11"].rank < got["O5"].rank
    assert sorted(p.rank for p in got.values()) == list(range(1, 51))


def test_the_reason_gives_the_ratings_and_the_score(objectives):
    got = by_id(objectives, rated(r1=(5, 4), r2=(2, 3)), {"r1": maps("r1", "O5"), "r2": maps("r2", "O5")})
    assert got["O5"].reasons[0].startswith("mitigates 2 risks rated 20 (Critical) and 6 (Medium): score 26")


# Key objectives

def test_by_default_the_first_seven_driven_by_a_high_risk_are_key(objectives):
    many = ["O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9"]
    got = by_id(objectives, rated(r1=(5, 4), r2=(2, 2)), {"r1": maps("r1", *many), "r2": maps("r2", "O10")})
    keys = [o for o, p in got.items() if p.key]
    assert len(keys) == KEY_BUDGET == 7
    assert set(keys) <= set(many)
    assert not got["O10"].key                 # only a Low risk drives it
    assert all(p.key == p.key_default for p in got.values())


def test_an_objective_no_high_risk_drives_is_not_key_by_default(objectives):
    got = by_id(objectives, rated(r1=(3, 3)), {"r1": maps("r1", "O1")})
    assert got["O1"].score == 9 and not got["O1"].key


def test_the_assessors_choice_wins(objectives):
    got = by_id(objectives, rated(r1=(5, 5), r2=(1, 1)), {"r1": maps("r1", "O1"), "r2": maps("r2", "O2")},
                keys={"O1": False, "O2": True})
    assert (got["O1"].key, got["O1"].key_default) == (False, True)
    assert (got["O2"].key, got["O2"].key_default) == (True, False)


def test_a_voluntary_objective_is_never_key_by_default(objectives):
    voluntary = next(o.id for o in objectives if o.note_tag == "VOLUNTARY")
    got = by_id(objectives, rated(r1=(5, 5)), {"r1": maps("r1", voluntary)})
    assert got[voluntary].score == 25 and not got[voluntary].key


def test_nothing_mapped_means_nothing_key(objectives):
    got = by_id(objectives, Severity(), {})
    assert not any(p.key for p in got.values())


def test_a_rating_for_a_risk_the_card_lacks_fails(objectives):
    with pytest.raises(ValueError, match="not in this system"):
        prioritise(objectives, rated(nope=(3, 3)), {}, RISKS)
