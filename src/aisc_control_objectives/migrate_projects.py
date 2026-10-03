"""Migrate every project database this service may enter.

`python -m aisc_control_objectives.migrate_projects` (the
control-objectives-migrate one-shot): reads the projects from `platform`
(`DATABASE_URL`), and for each one opens `project_<hex>` (`PROJECT_DATABASE_URL`)
and brings it to the head revision.

- A database that does not exist, that this role may not connect to, or whose
  `control_objectives` schema the platform has not made yet is skipped; the
  app migrates it on first open once it can.
- Any other error is permanent: it is reported and the next project is tried.

Exit status: 0 when every database it could enter is at head, 2 when any
failed permanently, 1 when the platform could not be read (the compose loop
retries 1 and stops on 2). It prints database names and error codes only,
never a URL or a statement. It does not import the access module: the
one-shot has no identity package on its path.
"""

from __future__ import annotations

import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from aisc_control_objectives import projectdb, settings


def _projects() -> list[str] | None:
    engine = projectdb.pooled_engine(settings.database_url())
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(text("SELECT pid::text FROM core.project ORDER BY pid")).scalars()
            )
    except SQLAlchemyError as exc:
        print(f"[control-objectives] the platform database cannot be read: {type(exc).__name__}",
              file=sys.stderr)
        return None
    finally:
        engine.dispose()


def _reason(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    code = getattr(orig, "sqlstate", None) or type(orig or exc).__name__
    message = getattr(diag, "message_primary", None) if diag is not None else None
    return f"{code} {message}" if message else str(code)


def migrate_one(name: str, template: str) -> str:
    """'migrated', 'skipped' or 'failed', printed with the reason."""
    engine = projectdb.make_engine(projectdb.project_url(template, name), poolclass=NullPool)
    try:
        try:
            with engine.connect():
                pass
        except DBAPIError as exc:
            if projectdb.is_missing_database(exc):
                print(f"skip {name}: the database does not exist")
                return "skipped"
            if projectdb.may_not_connect(exc):
                print(f"skip {name}: this role may not connect to it")
                return "skipped"
            print(f"FAILED {name}: {_reason(exc)}")
            return "failed"
        try:
            projectdb.migrate(engine)
        except projectdb.SchemaMissing:
            print(f"skip {name}: schema control_objectives is missing (platform template 0008 not applied)")
            return "skipped"
        except Exception as exc:  # every other error is permanent for this database
            print(f"FAILED {name}: {_reason(exc)}")
            return "failed"
        print(f"migrated {name}")
        return "migrated"
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    pids = _projects()
    if pids is None:
        return 1
    template = settings.project_database_url()
    outcomes = [migrate_one(projectdb.database_name(pid), template) for pid in pids]
    failed = outcomes.count("failed")
    print(f"[control-objectives] {outcomes.count('migrated')} migrated,"
          f" {outcomes.count('skipped')} skipped, {failed} failed")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
