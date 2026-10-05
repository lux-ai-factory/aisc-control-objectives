"""One database per project, and the only way into one.

Every project's assessments live in that project's own Postgres database,
`project_<pid without hyphens>`.
The `platform` database is read for one thing only: who is in which project
(`core.project`, `core.project_member`).

So there are two kinds of connection, and both are made here, the only module
that calls `create_engine(`:

- one engine on `platform` (pool 2), for membership;
- one engine per project database (pool 2, no overflow), at most
  `MAX_OPEN_PROJECTS` of them held open, the least recently used closed first.

`ProjectDatabases.open(project, caller, write)` is the door. It decides
membership first (`access.decide`, an admin counting as owner), resolves a slug to its pid, and only then builds a database name and
connects. A stranger, a viewer's write, a project that does not exist or a
membership that cannot be read never reaches a project database. The first
open of a database in this process migrates it (one migration however many
requests arrive at once); a database that has been dropped is forgotten and
reported as gone.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:  # access imports aisc_identity, which the migrate one-shot does not have
    from aisc_control_objectives.access import Access, Verdict

#: A platform project's pid, the only thing that may become part of a database name.
PID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: /app in the image, apps/control-objectives in a checkout: where alembic lives.
APP_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI, ALEMBIC_DIR = APP_ROOT / "alembic.ini", APP_ROOT / "alembic"

#: Project databases held open at once.
MAX_OPEN_PROJECTS = 20

#: One migration at a time in this process: Alembic keeps its context and op proxies in module
#: globals, so two databases migrated at once in two threads would overwrite each other's.
_MIGRATING = threading.Lock()


def database_name(pid: str) -> str:
    """`project_` + the pid, lower case, without hyphens. Anything else is a ValueError."""
    if not isinstance(pid, str) or not PID.fullmatch(pid.lower()):
        raise ValueError("not a project pid")
    return "project_" + pid.lower().replace("-", "")


def make_engine(url: str, **kwargs) -> Engine:
    """Every engine of the service is made here and nowhere else."""
    return create_engine(url, **kwargs)


def pooled_engine(url: str) -> Engine:
    """An engine that holds at most two connections."""
    return make_engine(url, pool_size=2, max_overflow=0, pool_pre_ping=True, pool_timeout=30)


def project_url(template: str, database: str) -> str:
    """The template with `{database}` replaced (never str.format: a password may hold braces)."""
    return template.replace("{database}", database)


class SchemaMissing(RuntimeError):
    """The project database has no `control_objectives` schema (template 0008 not applied)."""


def migrate(engine: Engine) -> None:
    """Bring one project database to the head revision, in one transaction."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def _orig(exc: BaseException) -> BaseException:
    return getattr(exc, "orig", None) or exc


def is_missing_database(exc: BaseException) -> bool:
    """Whether an error says the database does not exist (for example, it was dropped)."""
    orig = _orig(exc)
    if getattr(orig, "sqlstate", None) == "3D000":
        return True
    return re.search(r'database "[^"]+" does not exist', str(orig)) is not None


def may_not_connect(exc: BaseException) -> bool:
    """Whether an error says this role may not connect to the database."""
    orig = _orig(exc)
    if getattr(orig, "sqlstate", None) == "42501":
        return True
    return "permission denied for database" in str(orig)


#: One platform pool per URL in a process, whichever ProjectDatabases asks: the
#: budget is two connections to `platform` per process, not per app built.
_PLATFORM_ENGINES: dict[str, Engine] = {}
_PLATFORM_LOCK = threading.Lock()


def platform_engine(url: str) -> Engine:
    """The process's pooled engine on the platform database at `url`."""
    with _PLATFORM_LOCK:
        engine = _PLATFORM_ENGINES.get(url)
        if engine is None:
            engine = _PLATFORM_ENGINES[url] = pooled_engine(url)
        return engine


class ProjectDatabaseGone(LookupError):
    """The project's database does not exist (any more)."""


class Refused(Exception):
    """The door stays shut: `verdict` is not-found, forbidden or unavailable."""

    def __init__(self, verdict: Verdict):
        super().__init__(verdict)
        self.verdict = verdict


@dataclass(frozen=True)
class Opened:
    """A project database the caller was let into."""

    pid: str
    database: str
    engine: Engine
    access: Access


class ProjectDatabases:
    """The platform engine, and an LRU of project engines behind the door."""

    def __init__(
        self,
        platform_url: str,
        project_url_template: str,
        *,
        capacity: int = MAX_OPEN_PROJECTS,
        migrate: Callable[[Engine], None] = migrate,
    ):
        if "{database}" not in project_url_template:
            raise ValueError("the project database URL has no {database}")
        self._platform_url = platform_url
        self._template = project_url_template
        self._capacity = capacity
        self._migrate = migrate
        self._platform: Engine | None = None
        self._engines: OrderedDict[str, Engine] = OrderedDict()
        self._lock = threading.Lock()
        self._opening: dict[str, threading.Lock] = {}

    @property
    def platform(self) -> Engine:
        """The engine on `platform`, made on first use (building connects to nothing)."""
        with self._lock:
            if self._platform is None:
                self._platform = platform_engine(self._platform_url)
            return self._platform

    def open(self, project: str, caller, write: bool) -> Opened:
        """Membership first, then the database. Raises Refused or ProjectDatabaseGone."""
        from aisc_control_objectives.access import access_for, decide, project_pid

        access = access_for(self.platform, project, caller)
        verdict = decide("POST" if write else "GET", access)
        if verdict != "allow":
            raise Refused(verdict)
        try:
            pid = project_pid(self.platform, project)
        except SQLAlchemyError as exc:
            raise Refused("unavailable") from exc
        if pid is None:
            raise Refused("not-found")
        name = database_name(pid)
        return Opened(pid=pid, database=name, engine=self._engine_for(name), access=access)

    def _engine_for(self, name: str) -> Engine:
        with self._lock:
            engine = self._engines.get(name)
            if engine is not None:
                self._engines.move_to_end(name)
                return engine
            opening = self._opening.setdefault(name, threading.Lock())
        with opening:
            with self._lock:
                engine = self._engines.get(name)
                if engine is not None:
                    self._engines.move_to_end(name)
                    return engine
            engine = pooled_engine(project_url(self._template, name))
            try:
                with _MIGRATING:
                    self._migrate(engine)
            except Exception as exc:
                engine.dispose()
                if is_missing_database(exc):
                    raise ProjectDatabaseGone(name) from exc
                raise
            with self._lock:
                self._engines[name] = engine
                self._engines.move_to_end(name)
                while len(self._engines) > self._capacity:
                    _oldest, old = self._engines.popitem(last=False)
                    old.dispose()
                self._opening.pop(name, None)
            return engine

    def evict(self, pid_or_database: str) -> None:
        """Forget one project's engine (its database was dropped) and close it."""
        name = pid_or_database if pid_or_database.startswith("project_") else database_name(pid_or_database)
        with self._lock:
            engine = self._engines.pop(name, None)
        if engine is not None:
            engine.dispose()

    def dispose(self) -> None:
        """Close every engine (shutdown)."""
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            platform, self._platform = self._platform, None
        for engine in engines:
            engine.dispose()
        if platform is not None:
            platform.dispose()
