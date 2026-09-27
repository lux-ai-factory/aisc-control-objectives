"""WP7: an assessment is of one saved version of the project's AI card.

"Start assessment" takes no file. It asks the platform for the project's latest
system version, asks qualification for that version's card as JSON-LD, and
stores the assessment against that version (`project.system_id`, a pid in
the project database's `project.system`). One assessment per version; an older version's assessment is
kept as it was and can no longer be changed.

The two upstream services are real HTTP servers here (a stub on a free port),
so what is tested is the wire: the paths, the forwarded Authorization header,
and what happens when either one is down. Rule ids refer to
docs/superpowers/pipeline-2026-09-23/03-specs.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.projects import Projects

AUTH = "Bearer the-callers-own-token"

_LATEST = re.compile(r"^/projects/([^/]+)/system-versions/latest$")
_CARD = re.compile(r"^/qualification/p/([^/]+)/api/system-versions/([^/]+)/ontology\.jsonld$")


class Upstream:
    """The platform and qualification, as far as this service talks to them.

    `latest[project]` is the version row the platform returns (None means the
    project has no version: `200 null`). `cards[system_pid]` is the JSON-LD text
    qualification returns for that version (missing means 404). `down` makes
    every call answer 500.
    """

    def __init__(self):
        self.latest: dict[str, dict | None] = {}
        self.cards: dict[str, str] = {}
        self.down: set[str] = set()   # "platform", "qualification"
        self.calls: list[tuple[str, str | None]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: bytes, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                upstream.calls.append((self.path, self.headers.get("Authorization")))
                path = self.path.split("?", 1)[0]
                if found := _LATEST.match(path):
                    if "platform" in upstream.down:
                        return self._send(500, b'{"detail":"down"}')
                    row = upstream.latest.get(found.group(1))
                    return self._send(200, json.dumps(row).encode())
                if found := _CARD.match(path):
                    if "qualification" in upstream.down:
                        return self._send(500, b"down", "text/plain")
                    card = upstream.cards.get(found.group(2))
                    if card is None:
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, card.encode("utf-8"), "application/ld+json")
                return self._send(404, b"no such route", "text/plain")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def version(self, project: str, pid: str, number: int, card: str | None) -> None:
        self.latest[project] = {
            "pid": pid, "project_id": project, "number": number, "name": "MCAS",
            "version": f"1.{number}", "provider": "", "description": "",
            "created_at": "2026-09-23T20:00:00Z", "created_by": "someone",
        }
        if card is not None:
            self.cards[pid] = card

    def close(self):
        self._server.shutdown()


@pytest.fixture()
def upstream(monkeypatch):
    stub = Upstream()
    monkeypatch.setenv("PLATFORM_URL", stub.url)
    monkeypatch.setenv("QUALIFICATION_URL", stub.url + "/qualification")
    yield stub
    stub.close()


@pytest.fixture()
def card_v1(fixtures_dir) -> str:
    # Odd spacing on purpose: the stored bytes must be the served bytes, not a
    # re-serialisation of the parse (S7.5).
    return (fixtures_dir / "mcas.ontology.jsonld").read_text() + "\n  \n"


@pytest.fixture()
def card_v2(fixtures_dir) -> str:
    """v2's card: risk4 is gone and risk0 reads differently."""
    graph = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
    graph = [node for node in graph if "#risk4" not in node["@id"]]
    graph = copy.deepcopy(graph)
    for node in graph:
        if node["@id"].endswith("#risk0"):
            for key, values in node.items():
                if key.endswith("#fullLabel"):
                    values[0]["@value"] = "Applicant wrongly ranked high risk, as v2 says it"
    return json.dumps(graph, indent=1)


class _Mapper:
    def propose(self, risk, findings=()):
        from aisc_control_objectives.risk_mapping import Mapping

        return Mapping(risk_id=risk.id)


@pytest.fixture()
def client(repository, objectives, upstream):
    projects = Projects(repository, objectives, _Mapper(), model="fake/model")
    app = create_app(objectives, projects, base_config=RunConfig())
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def _start(client, project: str):
    """Click "Start assessment": a form post with no file."""
    return client.post(
        f"/p/{project}/projects", data={"name": "MCAS"}, headers={"Authorization": AUTH}
    )


def _assessment_id(response) -> str:
    assert response.status_code == 303, (response.status_code, response.text[:300])
    found = re.search(r"/projects/([^/?#]+)$", response.headers["location"])
    assert found, response.headers["location"]
    return found.group(1)


def _system_id(repository, assessment_id: str) -> str | None:
    with repository.engine.connect() as connection:
        return connection.execute(
            text("SELECT system_id::text FROM control_objectives.project WHERE id = :id"),
            {"id": assessment_id},
        ).scalar()


def _count(repository) -> int:
    with repository.engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar()


# ── S7.1 one assessment per version ─────────────────────────────────────────


def test_s7_1_starting_twice_on_v1_opens_the_same_assessment(
    client, repository, platform_project, system_version, upstream, card_v1
):
    """# S7.1"""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)

    first = _assessment_id(_start(client, platform_project))
    second = _assessment_id(_start(client, platform_project))

    assert first == second
    assert _count(repository) == 1
    assert _system_id(repository, first) == v1


# ── S7.2 the next version gets its own, the old one is read-only ────────────


def test_s7_2_v2_gets_its_own_assessment_and_v1s_is_left_as_it_was(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """# S7.2"""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    before = repository.get(a1)

    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)
    a2 = _assessment_id(_start(client, platform_project))

    assert a2 != a1
    assert _system_id(repository, a2) == v2
    assert _system_id(repository, a1) == v1
    after = repository.get(a1)
    assert after.jsonld == before.jsonld
    assert [r.id for r in after.ontology.risks] == [r.id for r in before.ontology.risks]


def test_s7_2_map_and_severity_on_a_non_latest_assessment_are_409(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """# S7.2: A1 is read-only once v2 exists."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)

    headers = {"Authorization": AUTH}
    mapped = client.post(f"/p/{platform_project}/projects/{a1}/map", headers=headers)
    rated = client.post(
        f"/p/{platform_project}/projects/{a1}/severity", data={"risk0": "5"}, headers=headers
    )

    assert mapped.status_code == 409
    assert rated.status_code == 409
    record = repository.get(a1)
    assert record.mapping_run is None
    assert record.severity.ratings == {}


def test_s7_2_the_pages_say_which_version_and_whether_it_is_read_only(
    client, platform_project, system_version, upstream, card_v1, card_v2
):
    """# S7.2: "Assessment of vN"; "read-only: vM is the latest" with the forms gone."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)
    a2 = _assessment_id(_start(client, platform_project))

    headers = {"Authorization": AUTH}
    old = client.get(f"/p/{platform_project}/projects/{a1}", headers=headers).text
    new = client.get(f"/p/{platform_project}/projects/{a2}", headers=headers).text

    assert "Assessment of v1" in old
    assert "read-only: v2 is the latest" in old
    assert f"/projects/{a1}/map" not in old
    assert f"/projects/{a1}/severity" not in old
    assert "Assessment of v2" in new
    assert "read-only" not in new


# ── S7.3 strangers see nothing ──────────────────────────────────────────────


@pytest.fixture()
def guarded(repository, objectives, upstream, monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "http://keycloak:8080/realms/aisc"
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", issuer)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak:8080/unused-in-tests")
    monkeypatch.setattr(
        "aisc_identity.service.key_for_jwks", lambda url: (lambda _t: key.public_key())
    )

    def token(subject):
        return jwt.encode(
            {"sub": subject, "preferred_username": subject, "iss": issuer,
             "exp": int(time.time()) + 300, "realm_access": {"roles": ["primary-user"]}},
            key, algorithm="RS256",
        )

    app = create_app(
        objectives, Projects(repository, objectives, _Mapper(), model="none"),
        engine=repository.engine,
    )
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False), token


def test_s7_3_a_stranger_cannot_start_or_open_an_assessment(
    guarded, repository, project_member, system_version, upstream, card_v1
):
    """# S7.3 (the control-objectives half; the qualification route is tested there)."""
    client, token = guarded
    pid, _, _ = project_member("owner")
    v1 = system_version(pid, 1)
    upstream.version(pid, v1, 1, card_v1)
    headers = {"Authorization": f"Bearer {token('nobody')}"}

    assert client.post(f"/p/{pid}/projects", data={}, headers=headers).status_code == 404
    assert client.get(f"/p/{pid}/projects", headers=headers).status_code == 404
    assert client.get(f"/p/{pid}/projects/whatever", headers=headers).status_code == 404
    assert upstream.calls == []        # nothing was asked upstream on a stranger's behalf
    assert _count(repository) == 0


# ── S7.4, S7.5 what is stored is the version's card ─────────────────────────


def test_s7_4_the_risks_stored_are_the_cards_risks_by_text_and_position(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """# S7.4"""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    _start(client, platform_project)
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)
    a2 = _assessment_id(_start(client, platform_project))

    expected = Ontology.from_jsonld(json.loads(card_v2)).risks
    stored = repository.get(a2).ontology.risks
    assert [(r.position, r.text) for r in stored] == [(r.position, r.text) for r in expected]
    assert len(stored) == 4


def test_s7_5_the_graph_is_the_served_bytes_and_its_digest_is_their_sha256(
    client, repository, platform_project, system_version, upstream, card_v1
):
    """# S7.5"""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))

    record = repository.get(a1)
    assert record.jsonld == card_v1
    assert record.digest == hashlib.sha256(card_v1.encode("utf-8")).hexdigest()


# ── S7.6 the version takes its assessment with it ───────────────────────────


def test_s7_6_deleting_the_version_deletes_its_assessment(
    client, repository, platform_project, system_version, upstream, card_v1
):
    """# S7.6"""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))

    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM project.system WHERE pid = :p"), {"p": v1})

    assert repository.get(a1) is None
    assert repository.orphan_rows() == 0


# (Isolation, S-D13: "deleting the project deletes versions and assessments"
# is gone from here. A deleted project's database is dropped whole, so there is
# no cascade left to test in it; the drop and the 404 after it are
# test_isolation_project_databases.py::test_I2_5_I17_1_a_dropped_database_is_evicted_and_answers_404
# and the platform's delete tests.)


# ── S7.7 the catalogue digest is per assessment ─────────────────────────────


def test_s7_7_a_new_catalogue_changes_the_digest_of_new_assessments_only(
    repository, objectives, platform_project, system_version, upstream, card_v1, card_v2
):
    """# S7.7 (DEFAULT, user to confirm)"""
    old_projects = Projects(repository, objectives, _Mapper())
    old = TestClient(create_app(objectives, old_projects), follow_redirects=False,
                     raise_server_exceptions=False)
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(old, platform_project))

    from aisc_control_objectives.db.repository import ProjectRepository

    newer = ProjectRepository(repository._url, objectives_digest="f" * 64)
    new = TestClient(create_app(objectives, Projects(newer, objectives, _Mapper())),
                     follow_redirects=False, raise_server_exceptions=False)
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)
    a2 = _assessment_id(_start(new, platform_project))

    assert repository.get(a1).objectives_digest == objectives.digest
    assert repository.get(a2).objectives_digest == "f" * 64


# ── the start flow's errors: nothing is stored ──────────────────────────────


def test_no_version_yet_is_409_and_nothing_is_stored(client, repository, platform_project, upstream):
    """WP7 interface: 409 "No AI card for the latest version yet"."""
    upstream.latest[platform_project] = None
    response = _start(client, platform_project)
    assert response.status_code == 409
    assert "No AI card for the latest version yet" in response.text
    assert _count(repository) == 0


def test_a_version_without_a_card_is_409_and_nothing_is_stored(
    client, repository, platform_project, system_version, upstream
):
    """WP7 interface: the qualification route 404s (vN has no card yet)."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card=None)
    response = _start(client, platform_project)
    assert response.status_code == 409
    assert "No AI card for the latest version yet" in response.text
    assert _count(repository) == 0


@pytest.mark.parametrize("which", ["platform", "qualification"])
def test_an_upstream_that_is_down_is_502_and_nothing_is_stored(
    client, repository, platform_project, system_version, upstream, card_v1, which
):
    """WP7 interface: 502 when qualification or the platform is down."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    upstream.down.add(which)
    response = _start(client, platform_project)
    assert response.status_code == 502
    assert _count(repository) == 0


def test_the_callers_authorization_is_what_reaches_both_upstreams(
    client, platform_project, system_version, upstream, card_v1
):
    """WP7 interface: upstream.py forwards the incoming Authorization header."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    _assessment_id(_start(client, platform_project))

    paths = {path.split("?", 1)[0] for path, _ in upstream.calls}
    assert f"/projects/{platform_project}/system-versions/latest" in paths
    assert f"/qualification/p/{platform_project}/api/system-versions/{v1}/ontology.jsonld" in paths
    assert {auth for _, auth in upstream.calls} == {AUTH}


# ── the upload is gone ──────────────────────────────────────────────────────


def test_the_projects_page_has_a_start_button_and_no_upload(client, platform_project):
    """WP7 interface: the upload field leaves the page."""
    page = client.get(f"/p/{platform_project}/projects", headers={"Authorization": AUTH}).text
    assert "Start assessment" in page
    assert 'type="file"' not in page


def test_the_json_upload_routes_are_removed(client, repository, platform_project, fixtures_dir):
    """WP7 interface: POST /api/projects and POST /api/projects/{id}/card are removed."""
    graph = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
    created = client.post(f"/api/projects?project={platform_project}", json=graph)
    replaced = client.post("/api/projects/abc123/card", json=graph)
    assert created.status_code in (404, 405)
    assert replaced.status_code in (404, 405)
    assert _count(repository) == 0


# ── upstream.py on its own ──────────────────────────────────────────────────


def test_upstream_latest_version_returns_the_platforms_row(upstream, platform_project):
    """WP7 interface: upstream.latest_version(project, token)."""
    from aisc_control_objectives import upstream as client_module

    upstream.version(platform_project, "11111111-1111-1111-1111-111111111111", 3, card=None)
    row = client_module.latest_version(platform_project, AUTH)
    assert row["pid"] == "11111111-1111-1111-1111-111111111111"
    assert row["number"] == 3
    assert upstream.calls[-1][1] == AUTH


def test_upstream_latest_version_is_none_when_there_is_none(upstream, platform_project):
    from aisc_control_objectives import upstream as client_module

    upstream.latest[platform_project] = None
    assert client_module.latest_version(platform_project, AUTH) is None


def test_upstream_card_jsonld_returns_the_bytes_as_served(upstream, platform_project, card_v1):
    """WP7 interface: upstream.card_jsonld(project, system_pid, token)."""
    from aisc_control_objectives import upstream as client_module

    pid = "22222222-2222-2222-2222-222222222222"
    upstream.cards[pid] = card_v1
    body = client_module.card_jsonld(platform_project, pid, AUTH)
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    assert body == card_v1
    assert upstream.calls[-1][1] == AUTH
