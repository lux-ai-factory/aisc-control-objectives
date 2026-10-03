"""Who may be in a project, and who may change it.

This service holds the risk ratings and the mappings for somebody's system, so
a signed-in account may read or change only the projects it is in. The
membership is the platform's to decide and lives in `core.project_member`,
which this service already has SELECT on: it reads the answer rather than
asking over HTTP, because it is looking at the same database.
"""

import pytest

from aisc_control_objectives.access import (
    Access,
    decide,
    project_from_path,
)


class TestWhichProjectARequestIsIn:
    def test_reads_the_slug_out_of_the_path(self):
        assert project_from_path("/p/mcas") == "mcas"
        assert project_from_path("/p/mcas/objectives") == "mcas"

    def test_does_not_decode_a_second_time(self):
        """The path is the router's, which the server has decoded once already.
        A second decode would make the gate check `a b` while the handler is
        given `a%20b`; the gate checks the very string the handler receives."""
        assert project_from_path("/p/a b/x") == "a b"
        assert project_from_path("/p/a%20b/x") == "a%20b"

    def test_is_none_outside_a_project(self):
        for path in ("/", "/objectives", "/health", "/static/app.css", "/p/", "/p"):
            assert project_from_path(path) is None, path


class TestWhatTheAnswerMeans:
    viewer = Access(role="viewer", admin=False)
    editor = Access(role="editor", admin=False)
    stranger = Access(role=None, admin=False)

    def test_a_stranger_is_told_the_project_does_not_exist(self):
        assert decide("GET", self.stranger) == "not-found"
        assert decide("POST", self.stranger) == "not-found"

    def test_a_member_may_read(self):
        assert decide("GET", self.viewer) == "allow"
        assert decide("HEAD", self.viewer) == "allow"

    def test_a_viewer_may_not_change_anything(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert decide(method, self.viewer) == "forbidden", method

    def test_an_editor_may(self):
        assert decide("POST", self.editor) == "allow"
        assert decide("DELETE", self.editor) == "allow"

    def test_no_answer_at_all_is_never_allowed(self):
        """Failing open would turn a database that is not there into an open
        door."""
        assert decide("GET", None) == "unavailable"
        assert decide("POST", None) == "unavailable"

    def test_an_admin_is_an_owner_everywhere(self):
        assert decide("DELETE", Access(role="owner", admin=True)) == "allow"


@pytest.mark.parametrize(
    "role,expected",
    [("viewer", "viewer"), ("editor", "editor"), ("owner", "owner"), (None, None)],
)
def test_the_role_is_read_from_the_shared_table(repository, project_member, role, expected):
    """Straight from core.project_member: the platform writes it, every module
    reads it, and there is no second copy to disagree."""
    from aisc_control_objectives.access import role_in_project

    _, slug, subject = project_member(role)
    assert role_in_project(repository._engine, slug, subject) == expected


def test_an_unknown_project_has_no_role(repository):
    from aisc_control_objectives.access import role_in_project

    assert role_in_project(repository._engine, "no-such-project", "nobody") is None


# The door itself
# The unit tests above say what the answer means. These say that every page and
# every endpoint under /p/{project} actually goes through it, which is the part
# that cannot be got right by remembering.


@pytest.fixture()
def guarded_app(repository, objectives, monkeypatch):
    """The app with the door fitted, and a stand-in for Keycloak."""
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from aisc_control_objectives.api.app import create_app
    from aisc_control_objectives.projects import Projects

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "http://keycloak:8080/realms/aisc"
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", issuer)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak:8080/unused-in-tests")
    monkeypatch.setattr(
        "aisc_identity.service.key_for_jwks", lambda url: (lambda _t: key.public_key())
    )

    def token(subject, roles=("primary-user",)):
        return jwt.encode(
            {
                "sub": subject,
                "preferred_username": subject,
                "iss": issuer,
                "exp": int(time.time()) + 300,
                "realm_access": {"roles": list(roles)},
            },
            key,
            algorithm="RS256",
        )

    app = create_app(
        objectives,
        Projects(repository=repository, catalogue=objectives, mapper=None, model="none"),
        engine=repository._engine,
    )
    return TestClient(app), token


def test_a_stranger_cannot_open_a_project_page(guarded_app, project_member):
    client, token = guarded_app
    pid, _, _ = project_member("owner")
    response = client.get(f"/p/{pid}", headers={"Authorization": f"Bearer {token('nobody')}"})
    assert response.status_code == 404


def test_a_member_can(guarded_app, project_member):
    client, token = guarded_app
    pid, _, subject = project_member("viewer")
    response = client.get(f"/p/{pid}", headers={"Authorization": f"Bearer {token(subject)}"})
    assert response.status_code == 200


def test_a_viewer_cannot_change_the_project(guarded_app, project_member):
    client, token = guarded_app
    pid, _, subject = project_member("viewer")
    response = client.post(
        f"/p/{pid}/projects", headers={"Authorization": f"Bearer {token(subject)}"}
    )
    assert response.status_code == 403


def test_an_editor_gets_past_the_door(guarded_app, project_member):
    """Past it the call answers on its own merits (here the platform is not
    configured, so starting an assessment is a 502). What matters is that the
    refusal is not about who is asking. "Start assessment" takes no file, so
    none is sent."""
    client, token = guarded_app
    pid, _, subject = project_member("editor")
    response = client.post(
        f"/p/{pid}/projects",
        headers={"Authorization": f"Bearer {token(subject)}"},
        data={"name": "anything"},
    )
    assert response.status_code not in (401, 403, 404)


def test_an_admin_gets_in_without_being_a_member(guarded_app, project_member):
    client, token = guarded_app
    pid, _, _ = project_member("owner")
    response = client.get(
        f"/p/{pid}", headers={"Authorization": f"Bearer {token('someone', ('admin',))}"}
    )
    assert response.status_code == 200


def test_no_token_on_a_project_page_is_401(guarded_app, project_member):
    client, _ = guarded_app
    pid, _, _ = project_member("owner")
    assert client.get(f"/p/{pid}").status_code == 401


def test_the_catalogue_of_objectives_is_the_same_for_everyone(guarded_app):
    """It has no project in its path and nothing of anybody's in it."""
    client, _ = guarded_app
    assert client.get("/objectives").status_code == 200


def test_the_health_check_stays_open(guarded_app):
    client, _ = guarded_app
    assert client.get("/health").status_code in (200, 404)
