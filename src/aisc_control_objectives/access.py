"""The gate in front of every route: who is calling, and whether they may be here.

Signing in happens at the gateway. This reads the verified caller, lets the
public reference pages through, and for anything under /p/{project} asks
aisc_identity.access through ProjectDatabases.open, so a project's database is
opened only for a caller admitted to that very project.
"""

from __future__ import annotations

import logging
import re
from contextlib import closing

from aisc_identity.access import READS, Refused, admit
from aisc_identity.service import Misconfigured, NotAuthenticated, caller_from_headers
from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

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

NOT_FOUND = Refused("not-found")


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


class ProjectAccess(BaseHTTPMiddleware):
    """Every request goes through this, and only the public paths pass unasked.

    A middleware rather than a dependency per route, for the same reason the
    catalogue guards its writes with one: a route added later is covered by
    having been added, instead of by somebody remembering to decorate it.

    It reads the path the router will match (`get_route_path`: the server's
    decoded path with the app's root path taken off, exactly as Starlette's
    router does), not the raw URL. Gating on the raw URL would let
    `/control-objectives/p/...` reach the /p/ pages ungated, because the router
    takes the root path off and the gate would not. The default is closed: a
    path that is not public needs a verified caller whatever it looks like,
    so a spelling the gate does not recognise is refused rather than let by.

    The caller is left on `request.state.caller`. With `databases` (the
    service as deployed) the gate is also the only way into a project's
    database: `databases.open` admits before it connects, and what it opened is
    left on `request.state.opened` for the handlers. Without it (the
    single-database domain tests) the gate admits only, and leaves the
    admission on `request.state.access`.
    """

    def __init__(self, app, engine, databases=None):
        super().__init__(app)
        self._engine = engine
        self._databases = databases

    @staticmethod
    def _refuse(path: str, refused: Refused):
        if _API_PATH.match(path):
            return JSONResponse({"detail": refused.message}, status_code=refused.status)
        return PlainTextResponse(refused.message, status_code=refused.status)

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
                return self._refuse(path, NOT_FOUND)
            try:
                if self._databases is not None:
                    from aisc_control_objectives.projectdb import ProjectDatabaseGone

                    write = request.method.upper() not in READS
                    try:
                        request.state.opened = await run_in_threadpool(
                            self._databases.open, project, caller, write)
                    except ProjectDatabaseGone:
                        return self._refuse(path, NOT_FOUND)
                else:
                    request.state.access = await run_in_threadpool(
                        admit, project, caller, request.method,
                        connect=lambda: closing(self._engine.raw_connection()))
            except Refused as refused:
                return self._refuse(path, refused)
        return await call_next(request)
