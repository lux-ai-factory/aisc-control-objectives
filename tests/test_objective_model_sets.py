"""An objective of a set the user made: its id is the set's code and a number, it
sorts after the built-in set, and its legal basis may cite something outside the AI Act and GDPR."""
from __future__ import annotations

import pytest

from aisc_control_objectives.models.control_objective import ControlObjective


def made(**over):
    fields = {"id": "BNK3", "macro_requirement": "R2 Technical Robustness and Safety", "legal_basis": "Internal policy 7",
                  "sub_requirement_label": "Model change approval", "text": "Every model change is approved.",
                  "assessment_mode": "Control", "target": "G", "standards_grounding": "", "grounding_tier_flag": ""}
    fields.update(over)
    return ControlObjective(**fields)


def test_a_set_objective_has_its_code_and_number():
    o = made()
    assert (o.set_code, o.number) == ("BNK", 3)
    assert o.macro_id == "R2"


def test_a_basis_outside_the_ai_act_and_gdpr_is_other_in_a_users_set():
    assert made().regimes == ["other"]


def test_the_built_in_set_still_refuses_an_unplaceable_basis():
    with pytest.raises(ValueError, match="fits no regime"):
        made(id="O7")


@pytest.mark.parametrize("bad", ["bnk3", "BNK", "BNK0", "TOOLONGX1", "R1.1", "B-1"])
def test_a_malformed_id_is_refused(bad):
    with pytest.raises(ValueError):
        made(id=bad)


def test_the_built_in_set_sorts_first_then_by_code_then_number():
    ids = ["BNK10", "AAA2", "O50", "BNK9", "O1"]
    objectives = [made(id=i, legal_basis="AI Act Art. 9") for i in ids]
    assert [o.id for o in sorted(objectives, key=lambda o: o.sort_key)] == ["O1", "O50", "AAA2", "BNK9", "BNK10"]
