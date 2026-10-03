"""Objective sets and objective profiles (2026-10-01), in one project's database.

Two levels, like the qualification app's question sets and questionnaires:

- An **objective set** is where objectives are written. Its code is chosen when it is made and
  starts every id in it: BNK1, BNK2, ... A number is never given twice; a retired objective keeps
  its. The set is edited as a draft and published as numbered versions that never change.
- An **objective profile** is a choice of objectives from the built-in set and from sets' published
  versions. Each save is a new version pinned to the set versions it took; a newer set version is
  offered, and taken only when the profile is saved again. An assessment pins one profile version.

The built-in set (O1 ... O50) and the Full AI Act profile are the packaged CSV: the same for every
project, read-only, and not stored.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db import tables
from aisc_control_objectives.models.control_objective import BUILTIN_CODE, ControlObjective

#: The built-in profile: every objective of the built-in set.
FULL_AI_ACT = "full-ai-act"
FULL_AI_ACT_NAME = "Full AI Act"

CODE = re.compile(r"^[A-Z]{2,6}$")
MODES = ("Control", "Test", "Control + Test")
#: The fields of an objective a user writes, as the form and the API name them.
FIELDS = ("dimension", "label", "text", "legal_basis", "assessment_mode", "target",
          "standards_grounding", "grounding_tier_flag", "notes")
#: What a legal basis left empty reads as.
NO_BASIS = "None stated"


@dataclass
class SetSummary:
    id: str
    code: str
    name: str
    description: str
    read_only: bool
    #: Objectives in the latest version (the built-in set: all of them).
    objectives: int
    #: The latest published version; None before the first.
    latest: int | None
    #: The draft differs from the latest version.
    unpublished: bool = False


@dataclass
class SetView:
    set: SetSummary
    #: Every objective of the draft, in number order, and whether it is retired.
    draft_rows: list[tuple[ControlObjective, bool]]
    #: (number, published_at, published_by), newest first.
    versions: list[tuple[int, datetime, str]]

    @property
    def draft(self) -> list[ControlObjective]:
        return [objective for objective, retired in self.draft_rows if not retired]


@dataclass
class ProfileSummary:
    id: str
    name: str
    description: str
    read_only: bool
    objectives: int
    #: The current version's number; None for the built-in profile.
    number: int | None
    #: A set it takes from has published a newer version.
    update_available: bool = False


@dataclass
class ProfileVersion:
    id: str | None
    number: int | None
    created_at: datetime | None = None
    created_by: str = ""


@dataclass
class ProfileView:
    profile: ProfileSummary
    current: ProfileVersion
    versions: list[ProfileVersion]
    #: The objective ids the current version takes, in order.
    picks: list[str]
    #: set code -> the set version the current version took.
    pins: dict[str, int] = field(default_factory=dict)
    #: set code -> (pinned, latest) where a newer version is out.
    updates: dict[str, tuple[int, int]] = field(default_factory=dict)


def _new_id() -> str:
    return uuid.uuid4().hex


class Library:
    """Sets and profiles of the project whose database `engine` is."""

    def __init__(self, engine, builtin: ControlObjectiveCatalogue):
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._builtin = builtin
        self._titles = {macro.id: macro.title for macro in builtin.macro_requirements()}

    # ── objectives ─────────────────────────────────────────────────────────

    @property
    def dimensions(self) -> dict[str, str]:
        """R1 ... R11 and their titles, in order."""
        return dict(self._titles)

    def _objective(self, objective_id: str, row) -> ControlObjective:
        return ControlObjective(
            id=objective_id,
            macro_requirement=f"{row.dimension} {self._titles.get(row.dimension, row.dimension)}",
            legal_basis=row.legal_basis or NO_BASIS,
            sub_requirement_label=row.label,
            text=row.text,
            assessment_mode=row.assessment_mode,
            target=row.target,
            standards_grounding=row.standards_grounding,
            grounding_tier_flag=row.grounding_tier_flag,
            notes=row.notes,
        )

    def _checked(self, objective_id: str, values: dict) -> dict:
        """The fields, cleaned and checked; ValueError naming the first that is wrong."""
        unknown = sorted(set(values) - set(FIELDS))
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(unknown)}")
        clean = {name: str(values.get(name) or "").strip() for name in FIELDS}
        if clean["dimension"] not in self._titles:
            raise ValueError(f"the dimension must be one of {', '.join(self._titles)}")
        if not clean["label"]:
            raise ValueError("a short label is required")
        if not clean["text"]:
            raise ValueError("the objective's text is required")
        if clean["assessment_mode"] not in MODES:
            raise ValueError(f"the assessment mode must be one of {', '.join(MODES)}")

        class _Row:
            pass

        row = _Row()
        row.__dict__.update(clean)
        try:
            self._objective(objective_id, row)
        except ValueError as exc:
            raise ValueError(f"{objective_id}: {exc}") from exc
        return clean

    # ── sets ───────────────────────────────────────────────────────────────

    def _latest_version(self, session: Session, set_id: str):
        return session.scalars(
            select(tables.ObjectiveSetVersion).where(tables.ObjectiveSetVersion.set_id == set_id)
            .order_by(tables.ObjectiveSetVersion.number.desc()).limit(1)).first()

    def _items(self, session: Session, set_version_id: str) -> list:
        return session.scalars(
            select(tables.ObjectiveSetVersionItem)
            .where(tables.ObjectiveSetVersionItem.set_version_id == set_version_id)
            .order_by(tables.ObjectiveSetVersionItem.position)).all()

    def _drafts(self, session: Session, set_id: str) -> list:
        return session.scalars(
            select(tables.ObjectiveDraft).where(tables.ObjectiveDraft.set_id == set_id)
            .order_by(tables.ObjectiveDraft.number)).all()

    @staticmethod
    def _content(rows) -> list[tuple]:
        return [(row.objective_id, *(getattr(row, name) for name in FIELDS)) for row in rows]

    def _summary(self, session: Session, row) -> SetSummary:
        latest = self._latest_version(session, row.id)
        items = self._items(session, latest.id) if latest else []
        active = [d for d in self._drafts(session, row.id) if not d.retired]
        return SetSummary(
            id=row.id, code=row.code, name=row.name, description=row.description, read_only=False,
            objectives=len(items), latest=latest.number if latest else None,
            unpublished=self._content(active) != self._content(items))

    def _builtin_summary(self) -> SetSummary:
        return SetSummary(id=BUILTIN_CODE, code=BUILTIN_CODE, name="AI Act control objectives",
                          description="The built-in set, from the AI Act's essential requirements.",
                          read_only=True, objectives=len(self._builtin), latest=1)

    def sets(self) -> list[SetSummary]:
        """The built-in set, then the project's sets by code."""
        with self._sessions() as session:
            rows = session.scalars(select(tables.ObjectiveSet).order_by(tables.ObjectiveSet.code)).all()
            return [self._builtin_summary()] + [self._summary(session, row) for row in rows]

    def _set_row(self, session: Session, set_id: str):
        row = session.get(tables.ObjectiveSet, set_id)
        if row is None:
            raise LookupError(f"no set {set_id}")
        return row

    # Each write takes `who_sub`, the author's Keycloak subject, kept beside the name shown (ledger
    # phase 6: authors by subject), and `record`, the caller's ledger events, run inside the write's
    # own transaction with what changed (R2.4).

    def create_set(self, code: str, name: str, description: str, *, who: str, who_sub: str = "",
                   record=None) -> SetSummary:
        code = (code or "").strip()
        if not CODE.match(code):
            raise ValueError("the code is 2 to 6 capital letters (A to Z), e.g. BNK")
        if not (name or "").strip():
            raise ValueError("a set needs a name")
        with self._sessions.begin() as session:
            taken = session.scalar(select(func.count()).select_from(tables.ObjectiveSet)
                                   .where(tables.ObjectiveSet.code == code))
            if taken:
                raise ValueError(f"the code {code} is already a set's in this project")
            row = tables.ObjectiveSet(id=_new_id(), code=code, name=name.strip(),
                                      description=(description or "").strip(), created_by=who,
                                      created_by_sub=who_sub)
            session.add(row)
            session.flush()
            if record is not None:
                record(session, {"id": row.id, "code": code, "name": row.name, "description": row.description})
            return self._summary(session, row)

    def get_set(self, set_id: str) -> SetView:
        with self._sessions() as session:
            row = self._set_row(session, set_id)
            drafts = self._drafts(session, set_id)
            versions = session.scalars(
                select(tables.ObjectiveSetVersion).where(tables.ObjectiveSetVersion.set_id == set_id)
                .order_by(tables.ObjectiveSetVersion.number.desc())).all()
            return SetView(
                set=self._summary(session, row),
                draft_rows=[(self._objective(d.objective_id, d), d.retired) for d in drafts],
                versions=[(v.number, v.published_at, v.published_by) for v in versions])

    def add_objective(self, set_id: str, values: dict, record=None) -> str:
        """A new objective in the draft; returns its id."""
        with self._sessions.begin() as session:
            row = self._set_row(session, set_id)
            objective_id = f"{row.code}{row.next_number}"
            clean = self._checked(objective_id, values)
            session.add(tables.ObjectiveDraft(set_id=set_id, number=row.next_number,
                                              objective_id=objective_id, **clean))
            row.next_number += 1
            if record is not None:
                record(session, {"id": objective_id, "after": clean})
            return objective_id

    def _draft_row(self, session: Session, set_id: str, objective_id: str):
        found = session.scalars(select(tables.ObjectiveDraft).where(
            tables.ObjectiveDraft.set_id == set_id,
            tables.ObjectiveDraft.objective_id == objective_id)).first()
        if found is None:
            raise LookupError(f"no objective {objective_id} in this set")
        return found

    def edit_objective(self, set_id: str, objective_id: str, values: dict, record=None) -> None:
        """The draft's wording; `record` gets it before and after (the draft keeps no versions)."""
        with self._sessions.begin() as session:
            draft = self._draft_row(session, set_id, objective_id)
            before = {name: getattr(draft, name) for name in FIELDS}
            for name, value in self._checked(objective_id, values).items():
                setattr(draft, name, value)
            after = {name: getattr(draft, name) for name in FIELDS}
            if record is not None and before != after:
                record(session, {"before": before, "after": after})

    def retire(self, set_id: str, objective_id: str, record=None) -> None:
        with self._sessions.begin() as session:
            draft = self._draft_row(session, set_id, objective_id)
            was, draft.retired = draft.retired, True
            if record is not None and not was:
                record(session, {})

    def restore(self, set_id: str, objective_id: str, record=None) -> None:
        with self._sessions.begin() as session:
            draft = self._draft_row(session, set_id, objective_id)
            was, draft.retired = draft.retired, False
            if record is not None and was:
                record(session, {})

    def publish(self, set_id: str, *, who: str, who_sub: str = "", record=None) -> int:
        """The draft becomes the next version; returns its number."""
        with self._sessions.begin() as session:
            self._set_row(session, set_id)
            active = [d for d in self._drafts(session, set_id) if not d.retired]
            if not active:
                raise ValueError("there is no objective to publish: add one, or restore a retired one")
            latest = self._latest_version(session, set_id)
            if latest is not None and self._content(active) == self._content(self._items(session, latest.id)):
                raise ValueError(f"nothing changed since version {latest.number}")
            version = tables.ObjectiveSetVersion(id=_new_id(), set_id=set_id,
                                                 number=(latest.number if latest else 0) + 1, published_by=who,
                                                 published_by_sub=who_sub)
            session.add(version)
            session.flush()
            for position, draft in enumerate(active, 1):
                session.add(tables.ObjectiveSetVersionItem(
                    set_version_id=version.id, objective_id=draft.objective_id, position=position,
                    **{name: getattr(draft, name) for name in FIELDS}))
            if record is not None:
                record(session, {"number": version.number, "items": [
                    {"id": d.objective_id, **{name: getattr(d, name) for name in FIELDS}} for d in active]})
            return version.number

    def set_version(self, set_id: str, number: int) -> list[ControlObjective]:
        with self._sessions() as session:
            version = session.scalars(select(tables.ObjectiveSetVersion).where(
                tables.ObjectiveSetVersion.set_id == set_id,
                tables.ObjectiveSetVersion.number == number)).first()
            if version is None:
                raise LookupError(f"no version {number} of this set")
            return [self._objective(item.objective_id, item) for item in self._items(session, version.id)]

    def delete_set(self, set_id: str, record=None) -> None:
        """An unpublished set and its drafts; `record` gets what they were (only the ledger keeps them)."""
        with self._sessions.begin() as session:
            row = self._set_row(session, set_id)
            if self._latest_version(session, set_id) is not None:
                raise ValueError("a published set cannot be deleted: profiles and assessments may rest on it")
            held = {"code": row.code, "name": row.name, "description": row.description, "drafts": [
                {"id": d.objective_id, "retired": d.retired, **{n: getattr(d, n) for n in FIELDS}}
                for d in self._drafts(session, set_id)]}
            session.delete(row)
            if record is not None:
                record(session, held)

    def available(self) -> ControlObjectiveCatalogue:
        """What a profile can pick: the built-in set and every set's latest published version."""
        with self._sessions() as session:
            picked = list(self._builtin)
            for row in session.scalars(select(tables.ObjectiveSet)).all():
                latest = self._latest_version(session, row.id)
                if latest is not None:
                    picked += [self._objective(i.objective_id, i) for i in self._items(session, latest.id)]
            return ControlObjectiveCatalogue(picked)

    def _latest_by_code(self, session: Session) -> dict[str, int]:
        out = {}
        for row in session.scalars(select(tables.ObjectiveSet)).all():
            latest = self._latest_version(session, row.id)
            if latest is not None:
                out[row.code] = latest.number
        return out

    # ── profiles ───────────────────────────────────────────────────────────

    def _profile_versions(self, session: Session, profile_id: str) -> list:
        return session.scalars(
            select(tables.ObjectiveProfileVersion).where(tables.ObjectiveProfileVersion.profile_id == profile_id)
            .order_by(tables.ObjectiveProfileVersion.number.desc())).all()

    def _profile_items(self, session: Session, version_id: str) -> list:
        return session.scalars(
            select(tables.ObjectiveProfileVersionItem)
            .where(tables.ObjectiveProfileVersionItem.profile_version_id == version_id)
            .order_by(tables.ObjectiveProfileVersionItem.position)).all()

    def _builtin_profile(self) -> ProfileSummary:
        return ProfileSummary(id=FULL_AI_ACT, name=FULL_AI_ACT_NAME,
                              description="Every objective of the built-in AI Act set.",
                              read_only=True, objectives=len(self._builtin), number=None)

    def _updates(self, items, latest: dict[str, int]) -> dict[str, tuple[int, int]]:
        pins = {i.set_code: i.set_version_number for i in items if i.set_code != BUILTIN_CODE}
        return {code: (pinned, latest[code]) for code, pinned in pins.items()
                if code in latest and latest[code] > pinned}

    def profiles(self) -> list[ProfileSummary]:
        """The built-in Full AI Act profile, then the project's by name."""
        with self._sessions() as session:
            latest = self._latest_by_code(session)
            out = [self._builtin_profile()]
            for row in session.scalars(select(tables.ObjectiveProfile).order_by(tables.ObjectiveProfile.name)).all():
                current = self._profile_versions(session, row.id)[0]
                items = self._profile_items(session, current.id)
                out.append(ProfileSummary(id=row.id, name=row.name, description=row.description, read_only=False,
                                          objectives=len(items), number=current.number,
                                          update_available=bool(self._updates(items, latest))))
            return out

    def get_profile(self, profile_id: str) -> ProfileView:
        if profile_id == FULL_AI_ACT:
            return ProfileView(profile=self._builtin_profile(), current=ProfileVersion(None, None), versions=[],
                               picks=[o.id for o in self._builtin])
        with self._sessions() as session:
            row = session.get(tables.ObjectiveProfile, profile_id)
            if row is None:
                raise LookupError(f"no profile {profile_id}")
            versions = self._profile_versions(session, profile_id)
            items = self._profile_items(session, versions[0].id)
            latest = self._latest_by_code(session)
            updates = self._updates(items, latest)
            as_version = [ProfileVersion(v.id, v.number, v.created_at, v.created_by) for v in versions]
            return ProfileView(
                profile=ProfileSummary(id=row.id, name=row.name, description=row.description, read_only=False,
                                       objectives=len(items), number=versions[0].number,
                                       update_available=bool(updates)),
                current=as_version[0], versions=as_version, picks=[i.objective_id for i in items],
                pins={i.set_code: i.set_version_number for i in items if i.set_code != BUILTIN_CODE},
                updates=updates)

    def _add_version(self, session: Session, profile_id: str, number: int, picks: list[str], who: str,
                     who_sub: str = "") -> str:
        available = self.available()
        latest = self._latest_by_code(session)
        version = tables.ObjectiveProfileVersion(id=_new_id(), profile_id=profile_id, number=number, created_by=who,
                                                 created_by_sub=who_sub)
        session.add(version)
        session.flush()
        chosen = sorted((available.by_id(oid) for oid in dict.fromkeys(picks)), key=lambda o: o.sort_key)
        for position, objective in enumerate(chosen, 1):
            code = objective.set_code
            session.add(tables.ObjectiveProfileVersionItem(
                profile_version_id=version.id, objective_id=objective.id, position=position, set_code=code,
                set_version_number=None if code == BUILTIN_CODE else latest[code]))
        return version.id

    def _check_picks(self, picks: list[str]) -> None:
        if not picks:
            raise ValueError("a profile takes at least one objective")
        available = self.available()
        missing = [oid for oid in picks if available.by_id(oid) is None]
        if missing:
            raise ValueError(f"cannot be picked (not in the built-in set or a published set): {', '.join(missing)}")

    def create_profile(self, name: str, description: str, picks: list[str], *, who: str, who_sub: str = "",
                       record=None) -> ProfileSummary:
        if not (name or "").strip():
            raise ValueError("a profile needs a name")
        picks = list(dict.fromkeys(picks))
        self._check_picks(picks)
        with self._sessions.begin() as session:
            row = tables.ObjectiveProfile(id=_new_id(), name=name.strip(),
                                          description=(description or "").strip(), created_by=who,
                                          created_by_sub=who_sub)
            session.add(row)
            session.flush()
            version_id = self._add_version(session, row.id, 1, picks, who, who_sub)
            if record is not None:
                record(session, {"id": row.id, "version_id": version_id, "name": row.name,
                                 "description": row.description, "picks": picks})
        return self.get_profile(row.id).profile

    def save_profile(self, profile_id: str, picks: list[str], *, who: str, name: str | None = None,
                     description: str | None = None, who_sub: str = "",
                     record=None) -> tuple[ProfileVersion, list[str]]:
        """The next version, pinned to the sets' latest versions. An objective the current version
        had and a newer set version retired is dropped, and returned so the page can say so."""
        current = self.get_profile(profile_id)
        if current.profile.read_only:
            raise ValueError("the built-in profile cannot be changed: make a profile of your own")
        picks = list(dict.fromkeys(picks))
        available = self.available()
        dropped = [oid for oid in picks if available.by_id(oid) is None and oid in current.picks]
        picks = [oid for oid in picks if oid not in dropped]
        self._check_picks(picks)
        with self._sessions.begin() as session:
            # locked, and its last number read under the lock: two saves at once take turns, each its own
            # number (phase 6 review m7)
            row = session.get(tables.ObjectiveProfile, profile_id, with_for_update=True)
            last = session.scalar(select(func.max(tables.ObjectiveProfileVersion.number))
                                  .where(tables.ObjectiveProfileVersion.profile_id == profile_id))
            named_before = {"name": row.name, "description": row.description}
            if name is not None:
                if not name.strip():
                    raise ValueError("a profile needs a name")
                row.name = name.strip()
            if description is not None:
                row.description = description.strip()
            number = (last or 0) + 1
            self._add_version(session, profile_id, number, picks, who, who_sub)
            if record is not None:
                record(session, {"number": number, "picks": picks, "dropped": dropped, "before": named_before,
                                 "after": {"name": row.name, "description": row.description}})
        return self.get_profile(profile_id).current, dropped

    def catalogue_of(self, profile_version_id: str | None) -> ControlObjectiveCatalogue:
        """The objectives an assessment on this profile version works with; None is Full AI Act."""
        if profile_version_id is None:
            return self._builtin
        with self._sessions() as session:
            version = session.get(tables.ObjectiveProfileVersion, profile_version_id)
            if version is None:
                raise LookupError(f"no profile version {profile_version_id}")
            sets = {row.code: row.id for row in session.scalars(select(tables.ObjectiveSet)).all()}
            out: list[ControlObjective] = []
            for item in self._profile_items(session, profile_version_id):
                if item.set_code == BUILTIN_CODE:
                    found = self._builtin.by_id(item.objective_id)
                    if found is not None:
                        out.append(found)
                    continue
                set_version = session.scalars(select(tables.ObjectiveSetVersion).where(
                    tables.ObjectiveSetVersion.set_id == sets.get(item.set_code),
                    tables.ObjectiveSetVersion.number == item.set_version_number)).first()
                worded = session.get(tables.ObjectiveSetVersionItem, (set_version.id, item.objective_id))
                out.append(self._objective(item.objective_id, worded))
            return ControlObjectiveCatalogue(out, digest=f"profile:{profile_version_id}")

    def version_info(self, profile_version_id: str | None) -> dict:
        """{"id", "version", "label", "update"} of the profile version an assessment pins; "update" is
        {"from", "to"} when the profile has a newer version."""
        if profile_version_id is None:
            return {"id": FULL_AI_ACT, "version": None, "label": FULL_AI_ACT_NAME, "update": None}
        with self._sessions() as session:
            version = session.get(tables.ObjectiveProfileVersion, profile_version_id)
            profile = session.get(tables.ObjectiveProfile, version.profile_id)
            newest = self._profile_versions(session, profile.id)[0].number
            return {"id": profile.id, "version": version.number,
                    "label": f"{profile.name}, version {version.number}",
                    "update": {"from": version.number, "to": newest} if newest > version.number else None}

    def profile_version_label(self, profile_version_id: str | None) -> str:
        """"Full AI Act" or "Bank, version 2"."""
        if profile_version_id is None:
            return FULL_AI_ACT_NAME
        with self._sessions() as session:
            version = session.get(tables.ObjectiveProfileVersion, profile_version_id)
            profile = session.get(tables.ObjectiveProfile, version.profile_id)
            return f"{profile.name}, version {version.number}"
