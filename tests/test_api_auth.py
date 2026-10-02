"""WP1 (api-auth 2026-09-25): every route asks who is calling, and an
assessment is decided by its own project.

Before this, the JSON API under /api/projects had no gate at all, and the
gate on /p/... could be walked around by adding the app's own root path
(/control-objectives/p/...), because the gate read the raw path while the
router read the path with the root path taken off. See
docs/superpowers/api-auth-2026-09-25/01-inventory.md, findings 1, 2, 5, 9b.

The gate here is the one the service really runs: `create_app(..., engine=)`
with a stand-in for Keycloak's signing key.
"""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import Mapping

ISSUER = "http://keycloak:8080/realms/aisc"
ROOT = "/control-objectives"


class _Mapper:
    def propose(self, risk, findings=()):
        return Mapping(risk_id=risk.id)


@pytest.fixture()
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISSUER)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak:8080/unused-in-tests")
    monkeypatch.setattr(
        "aisc_identity.service.key_for_jwks", lambda url: (lambda _t: key.public_key())
    )

    def token(subject, roles=("primary-user",)):
        return jwt.encode(
            {
                "sub": subject,
                "preferred_username": subject,
                "iss": ISSUER,
                "exp": int(time.time()) + 300,
                "realm_access": {"roles": list(roles)},
            },
            key,
            algorithm="RS256",
        )

    return token


@pytest.fixture()
def projects(repository, objectives):
    return Projects(repository, objectives, _Mapper(), model="none")


def _client(objectives, projects, repository, root_path=""):
    app = create_app(objectives, projects, engine=repository.engine, root_path=root_path)
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture()
def client(objectives, projects, repository):
    return _client(objectives, projects, repository)


@pytest.fixture()
def rooted(objectives, projects, repository):
    """The app as deployed: CONTROL_OBJECTIVES_ROOT_PATH=/control-objectives."""
    return _client(objectives, projects, repository, root_path=ROOT)


@pytest.fixture()
def assessment(projects, system_version, fixtures_dir):
    """`assessment(pid)`: a stored assessment of the project's version 1."""
    jsonld = (fixtures_dir / "mcas.ontology.jsonld").read_text()

    def make(pid: str) -> str:
        view = projects.create(
            project=pid, name="MCAS", jsonld=jsonld, raw=json.loads(jsonld),
            system_id=system_version(pid, 1),
        )
        return view.record.id

    return make


def _add_member(repository, pid: str, subject: str, role: str) -> None:
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO core.project_member (project_id, subject, role)"
                " VALUES (:pid, :subject, :role)"
            ),
            {"pid": pid, "subject": subject, "role": role},
        )


def _exists(repository, assessment_id: str) -> bool:
    with repository.engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM control_objectives.project WHERE id = :id"),
            {"id": assessment_id},
        ).scalar() == 1


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _risk_id(client, headers, pid, assessment_id) -> str:
    return client.get(
        f"/p/{pid}/api/projects/{assessment_id}", headers=headers
    ).json()["risks"][0]["id"]


# ── 1. no token, no entry ───────────────────────────────────────────────────


def _gated_routes(pid: str, aid: str) -> list[tuple[str, str]]:
    return [
        ("GET", "/"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", f"/p/{pid}/api/projects"),
        ("GET", f"/p/{pid}/api/projects/{aid}"),
        ("POST", f"/p/{pid}/api/projects/{aid}/map"),
        ("POST", f"/p/{pid}/api/projects/{aid}/severity"),
        ("DELETE", f"/p/{pid}/api/projects/{aid}"),
        ("GET", f"/p/{pid}"),
        ("GET", f"/p/{pid}/objectives"),
        ("GET", f"/p/{pid}/projects"),
        ("POST", f"/p/{pid}/projects"),
        ("GET", f"/p/{pid}/projects/{aid}"),
        ("POST", f"/p/{pid}/projects/{aid}/map"),
        ("POST", f"/p/{pid}/projects/{aid}/severity"),
        ("GET", "/no-such-route"),
    ]


def test_every_route_but_the_public_ones_is_401_without_a_token(
    client, signing, repository, project_member, assessment
):
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    for method, path in _gated_routes(pid, aid):
        kwargs = {"json": {}} if path.endswith("/severity") else {}
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.status_code)
    assert _exists(repository, aid)


def test_behind_the_root_path_it_is_the_same(rooted, signing, project_member, assessment):
    """The deployed app has root_path=/control-objectives, and Starlette serves a
    path that starts with it by taking it off. The gate reads the same path."""
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    for method, path in _gated_routes(pid, aid):
        kwargs = {"json": {}} if path.endswith("/severity") else {}
        for variant in (path, ROOT + path):
            response = rooted.request(method, variant, **kwargs)
            assert response.status_code == 401, (method, variant, response.status_code)


def test_the_public_catalogue_and_health_stay_open(client, rooted, signing):
    for app in (client, rooted):
        for prefix in ("", ROOT) if app is rooted else ("",):
            for path in (
                "/health",
                "/objectives",
                "/api/config",
                "/api/control-objectives",
                "/api/control-objectives?mode=test",
                "/api/control-objectives/O1",
                "/api/macro-requirements",
                "/static/laif-logo.svg",
            ):
                response = app.get(prefix + path)
                assert response.status_code == 200, (prefix + path, response.status_code)


def test_public_means_exactly_those_paths(client, signing, project_member):
    """A public prefix is not a way in to what is beside it."""
    for path in (
        "/objectives/x",
        "/api/control-objectives/O1/x",
        "/api/configx",
        "/static/a/b",
        "/healthz",
    ):
        assert client.get(path).status_code == 401, path


# ── 4. the path cannot be dressed up to miss the gate ───────────────────────


def _bypass_variants(pid: str, aid: str) -> list[str]:
    return [
        f"{ROOT}/p/{pid}/projects",
        f"{ROOT}/p/{pid}/projects/{aid}",
        f"{ROOT}{ROOT}/p/{pid}/projects/{aid}",
        f"/p/{pid}/projects/",
        f"/p/{pid}/projects/{aid}/",
        f"{ROOT}/p/{pid}/projects/{aid}/",
        f"/%70/{pid}/projects/{aid}",
        f"{ROOT}/%70/{pid}/projects/{aid}",
        f"/control%2Dobjectives/p/{pid}/projects/{aid}",
        f"/p%2F{pid}/projects/{aid}",
        f"/p/{pid}%2Fprojects/{aid}",
        f"//p/{pid}/projects/{aid}",
        f"{ROOT}//p/{pid}/projects/{aid}",
        f"/p/{pid}/api/projects/{aid}/",
        f"{ROOT}/p/{pid}/api/projects/{aid}",
        f"{ROOT}{ROOT}/p/{pid}/api/projects/{aid}",
        f"/p/{pid}/api/projects%2F{aid}",
        f"/p/{pid}/api//projects/{aid}",
    ]


def test_no_variant_of_a_gated_path_answers_without_a_token(
    rooted, signing, project_member, assessment
):
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    for path in _bypass_variants(pid, aid):
        response = rooted.get(path)
        assert response.status_code == 401, (path, response.status_code)


def test_no_variant_shows_a_stranger_the_assessment(rooted, signing, project_member, assessment):
    """With a valid token that is in no project: never a 2xx, never the data."""
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    headers = _bearer(signing("stranger"))
    for path in _bypass_variants(pid, aid):
        response = rooted.get(path, headers=headers)
        assert response.status_code in (404, 307), (path, response.status_code)
        assert "MCAS" not in response.text, path
        if response.status_code == 307:
            # only the router's own slash redirect, to a path that is gated too
            follow = rooted.get(response.headers["location"], headers=headers)
            assert follow.status_code == 404, (path, response.headers["location"])


def _asgi_get(app, path: str, headers: dict[str, str]) -> int:
    """GET with `path` as the server hands it to the app (decoded once).

    TestClient cannot do this: it decodes httpx's already-decoded path a second
    time, so `%2530` arrives as `0` rather than `%30`, which is not what uvicorn
    does.
    """
    import anyio

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    anyio.run(app, scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_the_project_in_the_path_is_read_once_as_the_router_reads_it(
    objectives, projects, repository, signing, project_member
):
    """`/p/%2530...` reaches the handler as project `%30...`. The gate used to
    decode it a second time and check `0...` instead; it must check the very
    string the handler gets."""
    pid, _, subject = project_member("owner")
    app = create_app(objectives, projects, engine=repository.engine)
    headers = _bearer(signing(subject))
    assert _asgi_get(app, f"/p/{pid}", headers) == 200
    decoded_once = f"%{ord(pid[0]):02x}" + pid[1:]
    assert _asgi_get(app, f"/p/{decoded_once}", headers) == 404


# ── 2. the JSON API: the assessment's own project decides ───────────────────


def test_a_viewer_reads_through_the_api(client, signing, project_member, assessment):
    pid, _, subject = project_member("viewer")
    aid = assessment(pid)
    headers = _bearer(signing(subject))
    listed = client.get(f"/p/{pid}/api/projects", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [aid]
    assert client.get(f"/p/{pid}/api/projects/{aid}", headers=headers).status_code == 200


def test_the_gateway_header_is_a_token_too(client, signing, project_member, assessment):
    pid, _, subject = project_member("viewer")
    aid = assessment(pid)
    headers = {"X-Auth-Request-Access-Token": signing(subject)}
    assert client.get(f"/p/{pid}/api/projects/{aid}", headers=headers).status_code == 200


def test_a_viewer_cannot_change_anything_through_the_api(
    client, signing, repository, project_member, assessment
):
    pid, _, subject = project_member("viewer")
    aid = assessment(pid)
    headers = _bearer(signing(subject))
    risk = _risk_id(client, headers, pid, aid)
    assert client.post(f"/p/{pid}/api/projects/{aid}/map", headers=headers).status_code == 403
    assert client.post(
        f"/p/{pid}/api/projects/{aid}/severity", headers=headers, json={risk: 3}
    ).status_code == 403
    assert client.delete(f"/p/{pid}/api/projects/{aid}", headers=headers).status_code == 403
    assert _exists(repository, aid)
    after = client.get(f"/p/{pid}/api/projects/{aid}", headers=headers).json()
    assert after["mapping_run"] is None


def test_an_editor_may(client, signing, repository, project_member, assessment):
    pid, _, subject = project_member("editor")
    aid = assessment(pid)
    headers = _bearer(signing(subject))
    risk = _risk_id(client, headers, pid, aid)
    assert client.post(f"/p/{pid}/api/projects/{aid}/map", headers=headers).status_code == 200
    rated = client.post(f"/p/{pid}/api/projects/{aid}/severity", headers=headers, json={risk: 3})
    assert rated.status_code == 200
    assert client.delete(f"/p/{pid}/api/projects/{aid}", headers=headers).status_code == 204
    assert not _exists(repository, aid)


def test_an_admin_may_without_being_a_member(client, signing, project_member, assessment):
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    headers = _bearer(signing("someone", ("admin",)))
    assert client.get(f"/p/{pid}/api/projects", headers=headers).status_code == 200
    assert client.get(f"/p/{pid}/api/projects/{aid}", headers=headers).status_code == 200
    assert client.post(f"/p/{pid}/api/projects/{aid}/map", headers=headers).status_code == 200


def test_a_stranger_is_told_nothing_exists(client, signing, repository, project_member, assessment):
    pid, _, _ = project_member("owner")
    aid = assessment(pid)
    headers = _bearer(signing("stranger"))
    unknown = client.get(f"/p/{pid}/api/projects/000000000000", headers=headers)
    assert unknown.status_code == 404
    for method, path in (
        ("GET", f"/p/{pid}/api/projects"),
        ("GET", f"/p/{pid}/api/projects/{aid}"),
        ("POST", f"/p/{pid}/api/projects/{aid}/map"),
        ("POST", f"/p/{pid}/api/projects/{aid}/severity"),
        ("DELETE", f"/p/{pid}/api/projects/{aid}"),
    ):
        kwargs = {"json": {"risk0": 3}} if path.endswith("/severity") else {}
        response = client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)
        if path.startswith(f"/p/{pid}/api/projects/"):
            # the same answer as an id that does not exist: no oracle
            assert response.json()["detail"].replace(aid, "X") == unknown.json()[
                "detail"
            ].replace("000000000000", "X"), path
    assert _exists(repository, aid)


def test_an_unknown_project_is_404(client, signing):
    headers = _bearer(signing("someone"))
    response = client.get(f"/p/{uuid.uuid4()}/api/projects", headers=headers)
    assert response.status_code == 404


def test_a_member_of_one_project_cannot_reach_anothers_assessment_by_id(
    client, signing, repository, project_member, assessment
):
    mine, _, subject = project_member("editor")
    theirs, _, _ = project_member("owner")
    aid = assessment(theirs)
    headers = _bearer(signing(subject))
    assert client.get(f"/p/{theirs}/api/projects/{aid}", headers=headers).status_code == 404
    assert client.post(f"/p/{theirs}/api/projects/{aid}/map", headers=headers).status_code == 404
    assert client.delete(f"/p/{theirs}/api/projects/{aid}", headers=headers).status_code == 404
    assert _exists(repository, aid)
    # and listing their project by pid is the same stranger's 404
    assert client.get(f"/p/{theirs}/api/projects", headers=headers).status_code == 404


# ── 3. the pages: an assessment is only under its own project ───────────────


def test_a_page_refuses_an_assessment_of_another_project(monkeypatch, fixtures_dir):
    """(Isolation, W-6 / O1.8 exception 1: rewritten in place on two real project
    databases, same assertions. With project_id gone, two projects can no
    longer share the one test database, so "another project's assessment" is
    one in another database, and the service is built as deployed.)"""
    from isolation_support import Projects as PlatformProjects
    from isolation_support import (
        cluster_or_skip,
        deployed_app,
        fake_upstream,
        signer,
        start_assessment,
    )

    cluster = cluster_or_skip()
    made = PlatformProjects(cluster)
    try:
        token = signer(monkeypatch)
        subject, owner = f"s-{uuid.uuid4().hex[:8]}", f"o-{uuid.uuid4().hex[:8]}"
        mine = made.make(members={subject: "editor"})
        theirs = made.make(members={owner: "owner"})
        latest = fake_upstream(monkeypatch, made, fixtures_dir)
        latest[theirs] = made.add_version(theirs, 1)
        client = TestClient(deployed_app(monkeypatch, cluster),
                            raise_server_exceptions=False, follow_redirects=False)
        aid = start_assessment(client, theirs, token(owner))
        headers = token(subject)
        for ref in (mine, made.slug(mine)):
            assert client.get(f"/p/{ref}/projects/{aid}", headers=headers).status_code == 404
            assert client.post(f"/p/{ref}/projects/{aid}/map", headers=headers).status_code == 404
            assert client.post(
                f"/p/{ref}/projects/{aid}/severity", headers=headers, data={"impact:risk0": "3"}
            ).status_code == 404
        with cluster.connect(made.database(theirs)) as connection:
            rated = connection.execute(
                "SELECT count(*) FROM control_objectives.risk WHERE project_id = %s"
                " AND rating_impact IS NOT NULL",
                (aid,),
            ).fetchone()[0]
        assert rated == 0
    finally:
        made.cleanup()


def test_a_page_opens_its_own_projects_assessment_by_pid_or_slug(
    client, signing, project_member, assessment
):
    pid, slug, subject = project_member("viewer")
    aid = assessment(pid)
    headers = _bearer(signing(subject))
    for ref in (pid, slug):
        response = client.get(f"/p/{ref}/projects/{aid}", headers=headers)
        assert response.status_code == 200, ref


def test_a_page_opens_by_slug_behind_the_root_path(rooted, signing, project_member, assessment):
    pid, slug, subject = project_member("viewer")
    aid = assessment(pid)
    headers = _bearer(signing(subject))
    assert rooted.get(f"{ROOT}/p/{slug}/projects/{aid}", headers=headers).status_code == 200
    assert rooted.get(f"/p/{slug}/projects/{aid}", headers=headers).status_code == 200


# ── 5. the caller's token reaches the platform and qualification ────────────


def test_start_assessment_forwards_the_gateway_token(client, signing, project_member, monkeypatch):
    """Behind Caddy the token arrives as X-Auth-Request-Access-Token and there is
    no Authorization header. The platform and qualification still get it."""
    import httpx

    pid, _, subject = project_member("editor")
    token = signing(subject)
    seen: list[dict] = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(dict(headers or {}))
        return httpx.Response(200, content=b"null", request=httpx.Request("GET", url))

    monkeypatch.setenv("PLATFORM_URL", "http://platform.invalid")
    monkeypatch.setenv("QUALIFICATION_URL", "http://qualification.invalid/qualification")
    monkeypatch.setattr("aisc_control_objectives.upstream.httpx.get", fake_get)

    response = client.post(
        f"/p/{pid}/projects", data={"name": "x"},
        headers={"X-Auth-Request-Access-Token": token},
    )
    assert response.status_code == 409  # the platform said: no version yet
    assert seen, "the platform was not asked"
    assert seen[0].get("Authorization") == f"Bearer {token}"
    assert seen[0].get("X-Auth-Request-Access-Token") == token


def test_upstream_sends_both_headers():
    from aisc_control_objectives import upstream

    assert upstream._headers("Bearer abc") == {
        "Authorization": "Bearer abc",
        "X-Auth-Request-Access-Token": "abc",
    }
    assert upstream._headers(None) == {}


# ── the service as composed runs with the gate ──────────────────────────────


def test_build_app_fits_the_gate(monkeypatch):
    """Without an engine create_app has no gate (the domain tests use that); the
    composition root must always pass one."""
    from aisc_control_objectives import server

    seen = {}

    def fake_create_app(*args, **kwargs):
        seen.update(kwargs)
        return object()

    class FakeDatabases:
        # (Isolation, O1.8 exception 2: the gate's engine is the platform
        # engine of ProjectDatabases now, not a repository's.)
        def __init__(self, *a, **k):
            self.platform = "the-engine"

    monkeypatch.setattr(server, "create_app", fake_create_app)
    monkeypatch.setattr(server, "ProjectDatabases", FakeDatabases)
    monkeypatch.setattr(server, "_build_completer", lambda config: (lambda *a, **k: ""))
    monkeypatch.setenv("BAF_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BAF_LLM_MODEL", "mistral:latest")
    server.build_app()
    assert seen.get("engine") == "the-engine"
