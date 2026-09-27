"""May this person be here, and may they change anything.

Signing in happens at the gateway and has already happened by the time a
request arrives. This answers the other question. A project belongs to the
people in it, the platform writes that down in `core.project_member`, and this
service reads it straight from the `platform` database (the one shared read it
keeps, I1.4), so there is no second copy to disagree and no HTTP call to fail.
Each project's assessments are in that project's own database, which is opened
only after this has said yes (projectdb.ProjectDatabases.open).

What is decided here is only the mapping from an answer to a status. The answer
itself comes from the row.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from aisc_identity.service import Misconfigured, NotAuthenticated, caller_from_headers
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

#: The realm role that administers the platform. Not a membership.
ADMIN_ROLE = "admin"

Verdict = Literal["allow", "not-found", "forbidden", "unavailable"]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Everything to do with one assessment lives under /p/{project}. `[^/]+` is
#: what the router's `{project}` matches, so the two agree on what the project is.
_PROJECT_PATH = re.compile(r"^/p/([^/]+)")

#: The only paths answered without a verified caller: the reference catalogue
#: of objectives, which reads the same for everyone and holds nothing of
#: anybody's, its logo, and the health check. Matched whole, on the path the
#: router matches, so a public prefix is not a way in to what is beside it.
_PUBLIC_PATH = re.compile(
    r"^(?:/health"
    r"|/objectives"
    r"|/api/config"
    r"|/api/macro-requirements"
    r"|/api/control-objectives(?:/[^/]+)?"
    r"|/static/[^/]+)$"
)

#: The JSON API of a project: its refusals are JSON, as its answers are.
_API_PATH = re.compile(r"^/p/[^/]+/api(?:/|$)")

#: least to most, as in the platform
_RANK = {"viewer": 0, "editor": 1, "owner": 2}


@dataclass(frozen=True)
class Access:
    #: viewer, editor, owner, or None for somebody who is not in the project
    role: str | None
    #: the realm role that administers the platform, which is not a membership
    admin: bool = False

    @property
    def may_write(self) -> bool:
        return _RANK.get(self.role or "", -1) >= _RANK["editor"]


def project_from_path(path: str) -> str | None:
    """The project a route path is inside, or None if it is not inside one.

    `path` is the path the router matches (the server has decoded it once, and
    the root path is off). It is not decoded again: the gate must check the
    very string the handler is given as `{project}`, or `/p/%2561bc` would be
    checked as `abc` and served as `%61bc`.
    """
    found = _PROJECT_PATH.match(path or "")
    return found.group(1) if found else None


def is_public(path: str) -> bool:
    """Whether a route path is answered without asking who is calling."""
    return bool(_PUBLIC_PATH.match(path or ""))


def decide(method: str, access: Access | None) -> Verdict:
    """What to do about a request, given what the database said.

    None means the question could not be answered, and then nobody gets in:
    failing open would turn a database that is not there into an open door.
    """
    if access is None:
        return "unavailable"
    if access.role is None:
        # Not 403: the slug is the project's name, often a customer's, and 403
        # would confirm it exists.
        return "not-found"
    if method.upper() in SAFE_METHODS:
        return "allow"
    return "allow" if access.may_write else "forbidden"


#: A project is named two ways: the pid rows point at, and the slug a URL can
#: read. A page here is opened with one or the other, and both name the same
#: project, so both are accepted. Compared as text, so a slug that is not a
#: uuid is a miss rather than a type error.
_MEMBERSHIP = text(
    "SELECT m.role FROM core.project_member m"
    "  JOIN core.project p ON p.pid = m.project_id"
    " WHERE (p.pid::text = :project OR p.slug = :project) AND m.subject = :subject"
)


_PID = text("SELECT pid::text FROM core.project WHERE pid::text = :project OR slug = :project")


def project_pid(engine, project: str) -> str | None:
    """The pid of a project named by its pid or its slug, or None if there is none."""
    with engine.connect() as connection:
        found = connection.execute(_PID, {"project": project}).first()
    return found[0] if found else None


def role_in_project(engine, project: str, subject: str) -> str | None:
    """What this person is to this project, straight from the shared table."""
    with engine.connect() as connection:
        found = connection.execute(
            _MEMBERSHIP, {"project": project, "subject": subject}
        ).first()
    return found[0] if found else None


def access_for(engine, project: str, caller) -> Access | None:
    """What this caller is to this project, counting the admin role.

    An admin is not a member; the realm role says it may act on any project,
    which is what keeps an orphaned assessment recoverable.
    """
    if caller.has_role(ADMIN_ROLE):
        return Access(role="owner", admin=True)
    try:
        return Access(role=role_in_project(engine, project, caller.subject))
    except SQLAlchemyError:
        logger.exception("could not read membership for %s", project)
        return None


#: What each refusal says. The same words wherever it is given.
REFUSALS = {
    "not-found": ("No such project.", 404),
    "forbidden": ("You can read this project but not change it.", 403),
    "unavailable": ("Who may be here cannot be established just now.", 503),
}


class ProjectAccess(BaseHTTPMiddleware):
    """Every request goes through this, and only the public paths pass unasked.

    A middleware rather than a dependency per route, for the same reason the
    catalogue guards its writes with one: a route added later is covered by
    having been added, instead of by somebody remembering to decorate it.

    It reads the path the router will match (`get_route_path`: the server's
    decoded path with the app's root path taken off, exactly as Starlette's
    router does), not the raw URL. Reading the raw URL is what let
    `/control-objectives/p/...` reach the /p/ pages ungated: the router took
    the root path off and the gate did not. And the default is closed: a
    path that is not public needs a verified caller whatever it looks like,
    so a spelling the gate does not recognise is refused rather than let by.

    The caller is left on `request.state.caller`. With `databases` (the
    service as deployed) the gate is also the only way into a project's
    database: `databases.open` decides membership before it connects, and what
    it opened is left on `request.state.opened` for the handlers, so an
    assessment is looked for only in the database of the project in the path.
    Without it (the single-database domain tests) the gate decides membership
    only, as before.
    """

    def __init__(self, app, engine, databases=None):
        super().__init__(app)
        self._engine = engine
        self._databases = databases

    @staticmethod
    def _refuse(path: str, message: str, status: int):
        if _API_PATH.match(path):
            return JSONResponse({"detail": message}, status_code=status)
        return PlainTextResponse(message, status_code=status)

    async def dispatch(self, request, call_next):
        path = get_route_path(request.scope)
        if is_public(path):
            return await call_next(request)
        try:
            caller = caller_from_headers(request.headers)
        except NotAuthenticated as exc:
            return PlainTextResponse(f"Sign in first: {exc}", status_code=401)
        except Misconfigured as exc:
            return PlainTextResponse(str(exc), status_code=500)
        request.state.caller = caller

        if path == "/p" or path.startswith("/p/"):
            project = project_from_path(path)
            if project is None:
                # Under /p/ but no project the router could read: nothing to serve.
                return self._refuse(path, *REFUSALS["not-found"])
            if self._databases is not None:
                from aisc_control_objectives.projectdb import ProjectDatabaseGone, Refused

                write = request.method.upper() not in SAFE_METHODS
                try:
                    opened = await run_in_threadpool(self._databases.open, project, caller, write)
                except Refused as refused:
                    return self._refuse(path, *REFUSALS[refused.verdict])
                except ProjectDatabaseGone:
                    return self._refuse(path, *REFUSALS["not-found"])
                request.state.opened = opened
            else:
                access = await run_in_threadpool(access_for, self._engine, project, caller)
                verdict = decide(request.method, access)
                if verdict != "allow":
                    return self._refuse(path, *REFUSALS[verdict])
        return await call_next(request)
