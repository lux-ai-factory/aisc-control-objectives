"""The Sets and Profiles pages and their API (2026-10-01).

Pages post forms and redirect (a refusal is a 400 naming what is wrong); the API speaks JSON (422
for a refusal, 404 for a set or profile the project does not have). Writing needs an editor, as
every non-GET route of a project does.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from aisc_control_objectives import ledger
from aisc_control_objectives.library import BUILTIN_CODE, FIELDS
from aisc_control_objectives.rendering import (
    render_profile_page,
    render_profiles_page,
    render_set_page,
    render_sets_page,
)


def _who(request: Request) -> str:
    caller = getattr(request.state, "caller", None)
    return (getattr(caller, "username", None) or getattr(caller, "subject", None) or "") if caller else ""


def _who_sub(request: Request) -> str:
    """The author's Keycloak subject, kept beside the name shown (ledger phase 6: authors by subject)."""
    caller = getattr(request.state, "caller", None)
    return (getattr(caller, "subject", None) or "") if caller else ""


def _set_view(view) -> dict:
    return {
        "set": asdict(view.set),
        "draft": [{**o.model_dump(), "retired": retired} for o, retired in view.draft_rows],
        "versions": [{"number": n, "published_at": at.isoformat(), "published_by": by} for n, at, by in view.versions],
    }


def _profile_view(view) -> dict:
    return {
        "profile": asdict(view.profile),
        "current": asdict(view.current),
        "versions": [asdict(v) for v in view.versions],
        "picks": view.picks,
        "pins": view.pins,
        "updates": {code: list(pair) for code, pair in view.updates.items()},
    }


def register_library(app, projects_of, root_path: str) -> None:
    def library(request: Request):
        return projects_of(request).library

    def page(project: str, path: str) -> RedirectResponse:
        return RedirectResponse(url=f"{root_path}/p/{project}{path}", status_code=303)

    async def form_fields(request: Request) -> dict:
        form = await request.form()
        return {name: str(form.get(name) or "") for name in FIELDS}

    # ── pages ──────────────────────────────────────────────────────────────

    @app.get("/p/{project}/sets", include_in_schema=False, response_class=HTMLResponse)
    def sets_page(project: str, request: Request) -> HTMLResponse:
        return HTMLResponse(render_sets_page(library(request), root_path=root_path, project=project))

    @app.post("/p/{project}/sets", include_in_schema=False)
    async def make_set_form(project: str, request: Request):
        form = await request.form()
        try:
            made = library(request).create_set(str(form.get("code") or ""), str(form.get("name") or ""),
                                               str(form.get("description") or ""), who=_who(request), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_set.created", item_type="objective_set", item_id=c["id"], details={"code": c["code"]},
                content=c))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid set: {exc}", status_code=400)
        return page(project, f"/sets/{made.id}")

    @app.get("/p/{project}/sets/{set_id}", include_in_schema=False, response_class=HTMLResponse)
    def set_page(project: str, set_id: str, request: Request):
        if set_id == BUILTIN_CODE:
            return page(project, "/objectives")
        try:
            return HTMLResponse(render_set_page(library(request), set_id, root_path=root_path, project=project))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/p/{project}/sets/{set_id}/objectives", include_in_schema=False)
    async def add_objective_form(project: str, set_id: str, request: Request):
        try:
            made = library(request).add_objective(set_id, await form_fields(request), record=lambda s, c: ledger.emit(
                s, "objective.added", item_type="objective", item_id=c["id"], details={"set": set_id},
                content=c["after"]))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid objective: {exc}", status_code=400)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, f"/sets/{set_id}#obj-{made}")

    @app.post("/p/{project}/sets/{set_id}/objectives/{objective_id}", include_in_schema=False)
    async def edit_objective_form(project: str, set_id: str, objective_id: str, request: Request):
        try:
            library(request).edit_objective(set_id, objective_id, await form_fields(request), record=lambda s, c: ledger.emit(
                s, "objective.edited", item_type="objective", item_id=objective_id, details={"set": set_id},
                content=c["after"], before=c["before"], after=c["after"]))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid objective: {exc}", status_code=400)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, f"/sets/{set_id}#obj-{objective_id}")

    @app.post("/p/{project}/sets/{set_id}/objectives/{objective_id}/{action}", include_in_schema=False)
    def retire_form(project: str, set_id: str, objective_id: str, action: str, request: Request):
        if action not in ("retire", "restore"):
            raise HTTPException(status_code=404)
        try:
            getattr(library(request), action)(set_id, objective_id, record=lambda s, c: ledger.emit(
                s, "objective.retired" if action == "retire" else "objective.restored", item_type="objective",
                item_id=objective_id, details={"set": set_id}))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, f"/sets/{set_id}#obj-{objective_id}")

    @app.post("/p/{project}/sets/{set_id}/publish", include_in_schema=False)
    def publish_form(project: str, set_id: str, request: Request):
        try:
            library(request).publish(set_id, who=_who(request), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_set.published", item_type="objective_set", item_id=set_id,
                item_version=str(c["number"]), details={"version": c["number"]}, content=c["items"]))
        except ValueError as exc:
            return PlainTextResponse(f"Cannot publish: {exc}", status_code=400)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, f"/sets/{set_id}")

    @app.post("/p/{project}/sets/{set_id}/delete", include_in_schema=False)
    def delete_set_form(project: str, set_id: str, request: Request):
        try:
            library(request).delete_set(set_id, record=lambda s, held: ledger.emit(
                s, "objective_set.deleted", item_type="objective_set", item_id=set_id, content=held))
        except ValueError as exc:
            return PlainTextResponse(f"Cannot delete: {exc}", status_code=400)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, "/sets")

    @app.get("/p/{project}/profiles", include_in_schema=False, response_class=HTMLResponse)
    def profiles_page(project: str, request: Request) -> HTMLResponse:
        return HTMLResponse(render_profiles_page(library(request), root_path=root_path, project=project))

    @app.get("/p/{project}/profiles/new", include_in_schema=False, response_class=HTMLResponse)
    def new_profile_page(project: str, request: Request) -> HTMLResponse:
        return HTMLResponse(render_profile_page(library(request), None, root_path=root_path, project=project))

    @app.post("/p/{project}/profiles", include_in_schema=False)
    async def make_profile_form(project: str, request: Request):
        form = await request.form()
        try:
            made = library(request).create_profile(
                str(form.get("name") or ""), str(form.get("description") or ""),
                [str(v) for v in form.getlist("objective")], who=_who(request), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_profile.created", item_type="objective_profile", item_id=c["id"], content=c))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid profile: {exc}", status_code=400)
        return page(project, f"/profiles/{made.id}")

    @app.get("/p/{project}/profiles/{profile_id}", include_in_schema=False, response_class=HTMLResponse)
    def profile_page(project: str, profile_id: str, request: Request) -> HTMLResponse:
        try:
            return HTMLResponse(render_profile_page(library(request), profile_id, root_path=root_path, project=project))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/p/{project}/profiles/{profile_id}", include_in_schema=False)
    async def save_profile_form(project: str, profile_id: str, request: Request):
        form = await request.form()
        try:
            library(request).save_profile(
                profile_id, [str(v) for v in form.getlist("objective")], who=_who(request),
                name=str(form.get("name") or ""), description=str(form.get("description") or ""), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_profile.version_saved", item_type="objective_profile", item_id=profile_id,
                item_version=str(c["number"]), details={"version": c["number"], "dropped": c["dropped"]},
                content={"picks": c["picks"], **c["after"]}, before=c["before"], after=c["after"]))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid profile: {exc}", status_code=400)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page(project, f"/profiles/{profile_id}")

    # ── API ────────────────────────────────────────────────────────────────

    def refused(exc: Exception) -> HTTPException:
        if isinstance(exc, LookupError):
            return HTTPException(status_code=404, detail=str(exc))
        return HTTPException(status_code=422, detail=str(exc))

    @app.get("/p/{project}/api/sets")
    def list_sets(project: str, request: Request) -> list[dict]:
        return [asdict(s) for s in library(request).sets()]

    @app.post("/p/{project}/api/sets", status_code=201)
    def make_set(project: str, request: Request, code: str = Body(...), name: str = Body(...),
                 description: str = Body("")) -> dict:
        try:
            return asdict(library(request).create_set(code, name, description, who=_who(request), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_set.created", item_type="objective_set", item_id=c["id"], details={"code": c["code"]},
                content=c)))
        except ValueError as exc:
            raise refused(exc) from exc

    @app.get("/p/{project}/api/sets/{set_id}")
    def get_set(project: str, set_id: str, request: Request) -> dict:
        try:
            return _set_view(library(request).get_set(set_id))
        except LookupError as exc:
            raise refused(exc) from exc

    @app.delete("/p/{project}/api/sets/{set_id}", status_code=204)
    def delete_set(project: str, set_id: str, request: Request) -> Response:
        try:
            library(request).delete_set(set_id, record=lambda s, held: ledger.emit(
                s, "objective_set.deleted", item_type="objective_set", item_id=set_id, content=held))
        except (ValueError, LookupError) as exc:
            raise refused(exc) from exc
        return Response(status_code=204)

    @app.post("/p/{project}/api/sets/{set_id}/objectives", status_code=201)
    def add_objective(project: str, set_id: str, request: Request, values: dict = Body(...)) -> dict:
        try:
            return {"id": library(request).add_objective(set_id, values, record=lambda s, c: ledger.emit(
                s, "objective.added", item_type="objective", item_id=c["id"], details={"set": set_id},
                content=c["after"]))}
        except (ValueError, LookupError) as exc:
            raise refused(exc) from exc

    @app.put("/p/{project}/api/sets/{set_id}/objectives/{objective_id}")
    def edit_objective(project: str, set_id: str, objective_id: str, request: Request,
                       values: dict = Body(...)) -> dict:
        try:
            library(request).edit_objective(set_id, objective_id, values, record=lambda s, c: ledger.emit(
                s, "objective.edited", item_type="objective", item_id=objective_id, details={"set": set_id},
                content=c["after"], before=c["before"], after=c["after"]))
            return _set_view(library(request).get_set(set_id))
        except (ValueError, LookupError) as exc:
            raise refused(exc) from exc

    @app.post("/p/{project}/api/sets/{set_id}/objectives/{objective_id}/{action}")
    def retire(project: str, set_id: str, objective_id: str, action: str, request: Request) -> dict:
        if action not in ("retire", "restore"):
            raise HTTPException(status_code=404)
        try:
            getattr(library(request), action)(set_id, objective_id, record=lambda s, c: ledger.emit(
                s, "objective.retired" if action == "retire" else "objective.restored", item_type="objective",
                item_id=objective_id, details={"set": set_id}))
            return _set_view(library(request).get_set(set_id))
        except LookupError as exc:
            raise refused(exc) from exc

    @app.post("/p/{project}/api/sets/{set_id}/publish")
    def publish(project: str, set_id: str, request: Request) -> dict:
        try:
            return {"version": library(request).publish(set_id, who=_who(request), who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_set.published", item_type="objective_set", item_id=set_id,
                item_version=str(c["number"]), details={"version": c["number"]}, content=c["items"]))}
        except (ValueError, LookupError) as exc:
            raise refused(exc) from exc

    @app.get("/p/{project}/api/sets/{set_id}/versions/{number}")
    def set_version(project: str, set_id: str, number: int, request: Request) -> list[dict]:
        try:
            return [o.model_dump() for o in library(request).set_version(set_id, number)]
        except LookupError as exc:
            raise refused(exc) from exc

    @app.get("/p/{project}/api/profiles")
    def list_profiles(project: str, request: Request) -> list[dict]:
        return [asdict(p) for p in library(request).profiles()]

    @app.post("/p/{project}/api/profiles", status_code=201)
    def make_profile(project: str, request: Request, name: str = Body(...), description: str = Body(""),
                     objective_ids: list[str] = Body(...)) -> dict:
        try:
            return asdict(library(request).create_profile(name, description, objective_ids, who=_who(request),
                                                          who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_profile.created", item_type="objective_profile", item_id=c["id"], content=c)))
        except ValueError as exc:
            raise refused(exc) from exc

    @app.get("/p/{project}/api/profiles/{profile_id}")
    def get_profile(project: str, profile_id: str, request: Request) -> dict:
        try:
            return _profile_view(library(request).get_profile(profile_id))
        except LookupError as exc:
            raise refused(exc) from exc

    @app.post("/p/{project}/api/profiles/{profile_id}/versions")
    def save_profile(project: str, profile_id: str, request: Request, objective_ids: list[str] = Body(...),
                     name: str | None = Body(None), description: str | None = Body(None)) -> dict:
        try:
            current, dropped = library(request).save_profile(
                profile_id, objective_ids, who=_who(request), name=name, description=description, who_sub=_who_sub(request), record=lambda s, c: ledger.emit(
                s, "objective_profile.version_saved", item_type="objective_profile", item_id=profile_id,
                item_version=str(c["number"]), details={"version": c["number"], "dropped": c["dropped"]},
                content={"picks": c["picks"], **c["after"]}, before=c["before"], after=c["after"]))
        except (ValueError, LookupError) as exc:
            raise refused(exc) from exc
        return {"current": asdict(current), "dropped": dropped}
