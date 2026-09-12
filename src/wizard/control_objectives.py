"""Load the AI Act control objectives from their CSV.

The CSV ships as package data so the service has its domain data with no
network and no database. Re-importing a newer CSV is a file swap: the column
names below are the contract, and a missing column, a malformed cell or a legal
basis the rules cannot place all fail at load time, naming the row, rather than
at the first request.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from wizard.models.control_objective import ControlObjective, MacroRequirement

#: CSV column -> model field. The CSV is authored elsewhere; this mapping is
#: the only place that knows its header spelling.
COLUMNS: dict[str, str] = {
    "ID": "id",
    "Macro_Requirement": "macro_requirement",
    "Legal_Basis": "legal_basis",
    "Sub_Requirement_Label": "sub_requirement_label",
    "Control_Objective": "text",
    "Assessment_Mode": "assessment_mode",
    "Target": "target",
    "Standards_Grounding": "standards_grounding",
    "Grounding_Tier_Flag": "grounding_tier_flag",
    "Notes": "notes",
}


def default_csv_path() -> Path:
    """The bundled objectives CSV."""
    return Path(__file__).resolve().parent / "data" / "ai_act_control_objectives.csv"


class ControlObjectiveCatalogue:
    """The full set of objectives, in requirement order."""

    def __init__(self, objectives: Iterable[ControlObjective]):
        self.objectives: list[ControlObjective] = sorted(
            objectives, key=lambda objective: objective.sort_key
        )
        self._by_id = {objective.id: objective for objective in self.objectives}
        self._macros = self._group_by_macro()

    def __len__(self) -> int:
        return len(self.objectives)

    def __iter__(self):
        return iter(self.objectives)

    def by_id(self, objective_id: str) -> ControlObjective | None:
        return self._by_id.get(objective_id)

    def macro_requirements(self) -> list[MacroRequirement]:
        """The macro requirements (R1 ... R11) with their objectives, in order."""
        return self._macros

    def _group_by_macro(self) -> list[MacroRequirement]:
        macros: dict[str, MacroRequirement] = {}
        for objective in self.objectives:
            macro = macros.get(objective.macro_id)
            if macro is None:
                macro = MacroRequirement(
                    id=objective.macro_id, title=objective.macro_title
                )
                macros[objective.macro_id] = macro
            macro.objectives.append(objective)
        return list(macros.values())

    def requiring_control(self) -> list[ControlObjective]:
        """Objectives needing an organisational control (paired ones included)."""
        return [objective for objective in self.objectives if objective.requires_control]

    def requiring_test(self) -> list[ControlObjective]:
        """Objectives needing a technical test (paired ones included)."""
        return [objective for objective in self.objectives if objective.requires_test]


def load_control_objectives(path: Path | str | None = None) -> ControlObjectiveCatalogue:
    """Read the objectives CSV (the bundled one unless `path` says otherwise)."""
    csv_path = Path(path) if path is not None else default_csv_path()
    # utf-8-sig: the exported CSV carries a BOM on the first header cell.
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{csv_path}: missing required column(s): {', '.join(missing)}"
            )
        objectives: list[ControlObjective] = []
        for number, row in enumerate(reader, start=2):  # 1 is the header
            fields = {field: (row.get(column) or "").strip() for column, field in COLUMNS.items()}
            if not fields["id"]:
                continue
            try:
                objectives.append(ControlObjective(**fields))
            except ValidationError as exc:
                raise ValueError(f"{csv_path}: row {number} (ID {fields['id']!r}): {exc}") from exc
    return ControlObjectiveCatalogue(objectives)
