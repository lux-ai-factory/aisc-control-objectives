"""An assessment is of one saved version of the project's AI card.

"Start assessment" takes no file. It asks the platform for the project's latest
system version, asks qualification for that version's card as JSON-LD, and
stores the assessment against that version (`project.system_id`, a pid in
the project database's `project.system`). One assessment per version; an older
version's assessment is kept as it was and is read-only.

The two upstream services are real HTTP servers here (a stub on a free port),
so what is tested is the wire: the paths, the forwarded Authorization header,
and what happens when either one is down.
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
    # re-serialisation of the parse.
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


# One assessment per version


def test_starting_twice_on_v1_opens_the_same_assessment(
    client, repository, platform_project, system_version, upstream, card_v1
):
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)

    first = _assessment_id(_start(client, platform_project))
    second = _assessment_id(_start(client, platform_project))

    assert first == second
    assert _count(repository) == 1
    assert _system_id(repository, first) == v1


# The next version gets its own, the earlier one is read-only


def test_v2_gets_its_own_assessment_and_v1s_is_left_as_it_was(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
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


def test_map_and_severity_on_a_non_latest_assessment_are_409(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """v1's assessment is read-only once v2 exists."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)

    headers = {"Authorization": AUTH}
    mapped = client.post(f"/p/{platform_project}/projects/{a1}/map", headers=headers)
    rated = client.post(
        f"/p/{platform_project}/projects/{a1}/severity", data={"impact:risk0": "5"}, headers=headers
    )

    assert mapped.status_code == 409
    assert rated.status_code == 409
    record = repository.get(a1)
    assert record.mapping_run is None
    assert record.severity.impact == {} and record.severity.likelihood == {}


def test_resetting_the_keys_of_a_non_latest_assessment_is_409(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """v1's assessment keeps its choices once v2 exists, and its page no longer offers the reset."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    headers = {"Authorization": AUTH}
    api_of_a1 = f"/p/{platform_project}/api/projects/{a1}"
    oid = client.get(api_of_a1, headers=headers).json()["priorities"][0]["objective_id"]
    assert client.post(f"{api_of_a1}/key", json={oid: True}, headers=headers).status_code == 200
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)

    form = client.post(f"/p/{platform_project}/projects/{a1}/keys/reset", data={"confirmed": "on"},
                       headers=headers)
    api = client.post(f"{api_of_a1}/keys/reset", headers=headers)

    assert form.status_code == 409 and api.status_code == 409
    assert repository.get(a1).keys == {oid: True}
    page = client.get(f"/p/{platform_project}/projects/{a1}?reset=ask", headers=headers).text
    assert "/keys/reset" not in page and "Reset keys to defaults" not in page


def test_the_pages_say_which_version_and_whether_it_is_read_only(
    client, platform_project, system_version, upstream, card_v1, card_v2
):
    """"Assessment of vN"; "read-only: vM is the latest" with the forms gone."""
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
    assert "Read-only: a newer AI card version (v2) has its own assessment." in old
    assert f"/projects/{a1}/map" not in old
    assert f"/projects/{a1}/severity" not in old
    assert "Assessment of v2" in new
    assert "Read-only" not in new


# Strangers see nothing


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


def test_a_stranger_cannot_start_or_open_an_assessment(
    guarded, repository, project_member, system_version, upstream, card_v1
):
    """The control-objectives half; the qualification route is tested there."""
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


# What is stored is the version's card


def test_the_risks_stored_are_the_cards_risks_by_text_and_position(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
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


def test_the_graph_is_the_served_bytes_and_its_digest_is_their_sha256(
    client, repository, platform_project, system_version, upstream, card_v1
):
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))

    record = repository.get(a1)
    assert record.jsonld == card_v1
    assert record.digest == hashlib.sha256(card_v1.encode("utf-8")).hexdigest()


# The version takes its assessment with it


def test_deleting_the_version_deletes_its_assessment(
    client, repository, platform_project, system_version, upstream, card_v1
):
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))

    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM project.system WHERE pid = :p"), {"p": v1})

    assert repository.get(a1) is None
    assert repository.orphan_rows() == 0


# A deleted project's database is dropped whole, so there is no cascade to test
# here; the drop and the 404 after it are tested in
# test_isolation_project_databases.py::test_a_dropped_database_is_evicted_and_answers_404
# and in the platform's delete tests.


# The catalogue digest is per assessment


def test_a_new_catalogue_changes_the_digest_of_new_assessments_only(
    repository, objectives, platform_project, system_version, upstream, card_v1, card_v2
):
    """A new catalogue changes the digest of new assessments only."""
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


# The start flow's errors: nothing is stored


def test_deleting_a_non_latest_assessment_is_409_and_keeps_it(
    client, repository, platform_project, system_version, upstream, card_v1, card_v2
):
    """An older version's assessment is kept as it was: DELETE answered 204
    to any editor, while every other write is 409 once a newer version exists."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a1 = _assessment_id(_start(client, platform_project))
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v2)

    gone = client.delete(f"/p/{platform_project}/api/projects/{a1}", headers={"Authorization": AUTH})

    assert gone.status_code == 409
    assert "Read-only" in gone.text
    assert repository.get(a1) is not None


def test_no_version_yet_is_409_and_nothing_is_stored(client, repository, platform_project, upstream):
    """409 "No AI card for the latest version yet"."""
    upstream.latest[platform_project] = None
    response = _start(client, platform_project)
    assert response.status_code == 409
    assert "No AI card for the latest version yet" in response.text
    assert _count(repository) == 0


def test_a_version_without_a_card_is_409_and_nothing_is_stored(
    client, repository, platform_project, system_version, upstream
):
    """the qualification route 404s (vN has no card yet)."""
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
    """502 when qualification or the platform is down."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    upstream.down.add(which)
    response = _start(client, platform_project)
    assert response.status_code == 502
    assert _count(repository) == 0


def test_the_callers_authorization_is_what_reaches_both_upstreams(
    client, platform_project, system_version, upstream, card_v1
):
    """upstream.py forwards the incoming Authorization header."""
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    _assessment_id(_start(client, platform_project))

    paths = {path.split("?", 1)[0] for path, _ in upstream.calls}
    assert f"/projects/{platform_project}/system-versions/latest" in paths
    assert f"/qualification/p/{platform_project}/api/system-versions/{v1}/ontology.jsonld" in paths
    assert {auth for _, auth in upstream.calls} == {AUTH}


# No upload


def test_the_projects_page_has_a_start_button_and_no_upload(client, platform_project):
    """the upload field leaves the page."""
    page = client.get(f"/p/{platform_project}/projects", headers={"Authorization": AUTH}).text
    assert "Start assessment" in page
    assert 'type="file"' not in page


@pytest.mark.parametrize("answer", ["html", "other-json", "card-not-utf8"])
def test_an_upstream_answer_of_the_wrong_shape_is_a_502(repository, objectives, monkeypatch, answer):
    """A 200 that is not the expected JSON (PLATFORM_URL pointed at the gateway answers its
    sign-in page) was a 500; the module promises UpstreamDown, a 502."""
    import uuid

    from fake_http import FakeServer

    fake = FakeServer()
    monkeypatch.setenv("PLATFORM_URL", fake.url)
    monkeypatch.setenv("QUALIFICATION_URL", fake.url + "/qualification")
    project, pid = "proj-c5", str(uuid.uuid4())
    latest = f"/projects/{project}/system-versions/latest"
    if answer == "html":
        fake.reply(latest, raw=b"<html>Sign in</html>", headers={"Content-Type": "text/html"})
    elif answer == "other-json":
        fake.reply(latest, body={"detail": "something else"})
    else:
        fake.reply(latest, body={"pid": pid, "project_id": project, "number": 1})
        fake.reply(f"/qualification/p/{project}/api/system-versions/{pid}/ontology.jsonld",
                   raw=b"\xff\xfe not utf-8", headers={"Content-Type": "application/ld+json"})
    try:
        projects = Projects(repository, objectives, _Mapper(), model="fake/model")
        app = create_app(objectives, projects, base_config=RunConfig())
        http = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        assert _start(http, project).status_code == 502
    finally:
        fake.close()
