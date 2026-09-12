"""The AI Act control objectives are the wizard's domain data.

The CSV in `data/` is the source of truth (it is the artefact the requirements
work produces); these tests pin the shape the rest of the service relies on, so
a re-import of a newer CSV that drops or renames a column fails here first.
"""

from __future__ import annotations

import pytest

from wizard.control_objectives import default_csv_path, load_control_objectives
from wizard.models.control_objective import ControlObjective


@pytest.fixture(scope="module")
def catalogue():
    return load_control_objectives()


def test_loads_every_objective_with_unique_ids(catalogue):
    assert len(catalogue.objectives) == 50
    ids = [objective.id for objective in catalogue.objectives]
    assert len(set(ids)) == 50


def test_objectives_are_in_requirement_order_not_string_order(catalogue):
    """R9.x sorts before R10.x — string sorting would put R10 first."""
    ids = [objective.id for objective in catalogue.objectives]
    assert ids[0] == "R1.1"
    assert ids[-1] == "R11.4"
    assert ids.index("R9.9") < ids.index("R10.1")


def test_first_objective_carries_every_column_verbatim(catalogue):
    objective = catalogue.by_id("R1.1")
    assert objective.macro_requirement == "R1 Human Agency and Oversight"
    assert objective.legal_basis == "AI Act Art. 14"
    assert objective.sub_requirement_label == "Operator oversight capability"
    assert objective.text.startswith("A qualified operator can monitor")
    assert objective.text.endswith("documented procedures and interfaces.")
    assert objective.assessment_mode == "Control"
    assert objective.standards_grounding == "ISO/IEC 42001 Ann. A.9"
    assert objective.grounding_tier_flag == "Tier 3"
    assert objective.notes == ""


def test_every_objective_has_text_and_a_legal_basis(catalogue):
    for objective in catalogue.objectives:
        assert objective.text.strip()
        assert objective.legal_basis.strip()


def test_by_id_is_exact_and_unknown_ids_return_none(catalogue):
    assert isinstance(catalogue.by_id("R9.1"), ControlObjective)
    assert catalogue.by_id("R99.1") is None


class TestMacroRequirements:
    def test_eleven_macro_requirements_in_numeric_order(self, catalogue):
        macros = catalogue.macro_requirements()
        assert [macro.id for macro in macros] == [f"R{n}" for n in range(1, 12)]

    def test_macro_splits_id_from_title(self, catalogue):
        macro = catalogue.macro_requirements()[0]
        assert macro.id == "R1"
        assert macro.title == "Human Agency and Oversight"

    def test_objectives_are_grouped_under_their_macro(self, catalogue):
        counts = {macro.id: len(macro.objectives) for macro in catalogue.macro_requirements()}
        assert counts["R9"] == 9
        assert counts["R6"] == 2
        assert sum(counts.values()) == 50

    def test_every_objective_knows_its_macro(self, catalogue):
        objective = catalogue.by_id("R9.1")
        assert objective.macro_id == "R9"
        assert objective.macro_title == "Risk Management"


class TestAssessmentMode:
    def test_only_the_three_known_modes_occur(self, catalogue):
        modes = {objective.assessment_mode for objective in catalogue.objectives}
        assert modes == {"Control", "Test", "Control + Test"}

    def test_paired_objectives_count_as_both(self, catalogue):
        # 36 Control + 9 Test + 5 Control + Test
        assert len(catalogue.requiring_control()) == 41
        assert len(catalogue.requiring_test()) == 14

    def test_a_paired_objective_appears_in_both_partitions(self, catalogue):
        paired = catalogue.by_id("R2.1")
        assert paired.assessment_mode == "Control + Test"
        assert paired.requires_control and paired.requires_test
        assert paired in catalogue.requiring_control()
        assert paired in catalogue.requiring_test()


class TestTarget:
    def test_the_raw_target_cell_is_kept_but_never_split(self, catalogue):
        """The column stays for fidelity to the CSV; nothing derives from it."""
        objective = catalogue.by_id("R2.1")
        assert objective.target == "G, M / M"
        assert not hasattr(objective, "control_targets")
        assert not hasattr(objective, "test_targets")


class TestLegalBasis:
    def test_multiple_instruments_split_on_semicolon(self, catalogue):
        objective = next(
            o for o in catalogue.objectives if ";" in o.legal_basis
        )
        assert len(objective.legal_bases) >= 2
        assert all(basis.strip() == basis for basis in objective.legal_bases)

    def test_a_single_basis_is_a_one_item_list(self, catalogue):
        assert catalogue.by_id("R1.1").legal_bases == ["AI Act Art. 14"]


class TestNotes:
    def test_notes_are_preserved_where_present(self, catalogue):
        assert catalogue.by_id("R2.1").notes.startswith("Paired:")
        assert "GAP:" in catalogue.by_id("R2.3").notes

    def test_objectives_without_notes_get_an_empty_string(self, catalogue):
        assert catalogue.by_id("R1.1").notes == ""


def test_loading_an_explicit_path_works(tmp_path):
    """A newer CSV can be pointed at without touching the package."""
    source = default_csv_path()
    copy = tmp_path / "objectives.csv"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(load_control_objectives(copy).objectives) == 50


def test_a_csv_missing_a_required_column_fails_loudly(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text('"ID","Macro_Requirement"\n"R1.1","R1 Human Agency"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="column"):
        load_control_objectives(bad)


class TestLoadTimeValidation:
    """The loader's contract is that a bad export fails at load, naming the
    row, never at the first request."""

    @staticmethod
    def _csv_with(tmp_path, overrides):
        import csv

        source = default_csv_path()
        rows = list(csv.DictReader(source.open(encoding="utf-8")))
        for row in rows:
            for (target_id, column), value in overrides.items():
                if row["ID"] == target_id:
                    row[column] = value
        out = tmp_path / "objectives.csv"
        with out.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows)
        return out

    def test_a_legal_basis_fitting_no_regime_fails_at_load_naming_the_row(self, tmp_path):
        path = self._csv_with(tmp_path, {("R1.1", "Legal_Basis"): "Codex Hammurabi §1"})
        with pytest.raises(ValueError, match=r"(?s)R1\.1.*regime"):
            load_control_objectives(path)

    def test_a_conditional_note_with_an_unknown_trigger_fails_at_load(self, tmp_path):
        path = self._csv_with(
            tmp_path, {("R2.1", "Notes"): "CONDITIONAL: applies only to systems generating deepfakes."}
        )
        with pytest.raises(ValueError, match=r"(?s)R2\.1.*trigger"):
            load_control_objectives(path)

    def test_a_blank_macro_requirement_fails_at_load(self, tmp_path):
        path = self._csv_with(tmp_path, {("R3.2", "Macro_Requirement"): ""})
        with pytest.raises(ValueError, match=r"R3\.2"):
            load_control_objectives(path)

    def test_a_malformed_id_fails_at_load(self, tmp_path):
        path = self._csv_with(tmp_path, {("R4.4", "ID"): "R4.4a"})
        with pytest.raises(ValueError, match=r"R4\.4a"):
            load_control_objectives(path)

    def test_an_id_under_the_wrong_macro_fails_at_load(self, tmp_path):
        path = self._csv_with(tmp_path, {("R5.1", "Macro_Requirement"): "R7 Accountability"})
        with pytest.raises(ValueError, match=r"R5\.1"):
            load_control_objectives(path)
