"""What the step 2 tests share that is not a fixture: the fake mappers and the API's paths.
The fixtures built on them (mapper, client, graph, start) are in conftest.py."""
from __future__ import annotations

from aisc_control_objectives.risk_mapping import MappedObjective, Mapping


class FakeMapper:
    BY_RISK = {
        "risk0": ["O11", "O21", "O9"],
        "risk1": ["O21", "O22", "O23"],
        "risk2": ["O1", "O4", "O2"],
        "risk3": ["O17", "O19", "O6"],
        "risk4": ["O7", "O16", "O11"],
    }

    def propose(self, risk, findings=()):
        quote = risk.text[:30]
        return Mapping(
            risk_id=risk.id,
            objectives=[
                MappedObjective(objective_id=oid, quote=quote, rationale="because")
                for oid in self.BY_RISK.get(risk.id, [])
            ],
        )


class SwitchableMapper:
    """FakeMapper whose answers the test can change between two mappings."""

    def __init__(self):
        self.by_risk = {rid: list(oids) for rid, oids in FakeMapper.BY_RISK.items()}

    def propose(self, risk, findings=()):
        return Mapping(
            risk_id=risk.id,
            objectives=[
                MappedObjective(objective_id=oid, quote=risk.text[:30], rationale="because")
                for oid in self.by_risk.get(risk.id, [])
            ],
        )


def in_order(ids) -> list[str]:
    """Catalogue order, as the selection is kept: O9 before O10."""
    return sorted(ids, key=lambda oid: int(oid[1:]))


def _api(platform_project, assessment, suffix=""):
    return f"/p/{platform_project}/api/projects/{assessment}{suffix}"
