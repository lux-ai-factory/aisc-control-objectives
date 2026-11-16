"""A project viewer reads the assessment and the objective library without being offered anything they would
be refused: no form that posts, and one line saying they can read but not change this project. An editor keeps
the forms. The gate here is the one the service runs, with a stand-in for Keycloak's signing key."""
from __future__ import annotations

import json
import re
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import Mapping

ISSUER = "http://keycloak:8080/realms/aisc"


class _NoModel:
    def propose(self, risk, findings=()):
        return Mapping(risk_id=risk.id)


@pytest.fixture()
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISSUER)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak:8080/unused-in-tests")
    monkeypatch.setattr("aisc_identity.service.key_for_jwks", lambda url: (lambda _t: key.public_key()))

    def token(subject):
        return jwt.encode({"sub": subject, "preferred_username": subject, "iss": ISSUER,
                           "exp": int(time.time()) + 300, "realm_access": {"roles": ["primary-user"]}},
                          key, algorithm="RS256")

    return token


@pytest.fixture()
def gated(repository, objectives):
    projects = Projects(repository, objectives, _NoModel(), model="none")
    app = create_app(objectives, projects, engine=repository.engine)
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False), projects


def _member_with_assessment(gated, project_member, system_version, fixtures_dir, role):
    _, projects = gated
    pid, _, subject = project_member(role)
    jsonld = (fixtures_dir / "mcas.ontology.jsonld").read_text()
    view = projects.create(project=pid, name="MCAS", jsonld=jsonld, raw=json.loads(jsonld),
                           system_id=system_version(pid, 1))
    return pid, subject, view.record.id


def _posting_forms(html: str) -> list[str]:
    return re.findall(r'<form\b[^>]*method="post"[^>]*>', html, flags=re.I)


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


PAGES = ["/projects/{a}", "/sets"]


@pytest.mark.parametrize("path", PAGES)
def test_a_viewer_is_offered_no_form_that_would_be_refused(gated, signing, project_member, system_version,
                                                          fixtures_dir, path):
    http, _ = gated
    pid, subject, a = _member_with_assessment(gated, project_member, system_version, fixtures_dir, "viewer")
    r = http.get(f"/p/{pid}" + path.format(a=a), headers={"Authorization": f"Bearer {signing(subject)}"})
    assert r.status_code == 200, r.text
    forms = _posting_forms(r.text)
    assert forms == [], f"a viewer is offered {len(forms)} forms on {path}: {forms[:3]}"


def test_a_viewer_is_not_offered_to_start_an_assessment(gated, signing, project_member):
    http, _ = gated
    pid, _, subject = project_member("viewer")
    r = http.get(f"/p/{pid}/projects", headers={"Authorization": f"Bearer {signing(subject)}"})
    assert r.status_code == 200, r.text
    assert "Start assessment" not in _shown(r.text) and _posting_forms(r.text) == [], \
        "a viewer is offered Start assessment"


def test_a_viewer_is_not_offered_a_new_profile(gated, signing, project_member):
    http, _ = gated
    pid, _, subject = project_member("viewer")
    r = http.get(f"/p/{pid}/profiles", headers={"Authorization": f"Bearer {signing(subject)}"})
    assert r.status_code == 200, r.text
    assert "New profile" not in _shown(r.text), "a viewer is offered New profile"


def test_the_assessment_page_tells_a_viewer_they_can_read_but_not_change(gated, signing, project_member,
                                                                         system_version, fixtures_dir):
    http, _ = gated
    pid, subject, a = _member_with_assessment(gated, project_member, system_version, fixtures_dir, "viewer")
    shown = _shown(http.get(f"/p/{pid}/projects/{a}", headers={"Authorization": f"Bearer {signing(subject)}"}).text)
    assert re.search(r"can read this project but not change it", shown, re.I), \
        "the page does not tell a viewer they can read but not change this project"


def test_an_editor_keeps_the_forms(gated, signing, project_member, system_version, fixtures_dir):
    http, _ = gated
    pid, subject, a = _member_with_assessment(gated, project_member, system_version, fixtures_dir, "editor")
    r = http.get(f"/p/{pid}/projects/{a}", headers={"Authorization": f"Bearer {signing(subject)}"})
    assert r.status_code == 200 and _posting_forms(r.text), "an editor lost the assessment's forms"


def test_a_viewer_reads_a_set_and_a_profile_without_their_forms(gated, signing, project_member):
    """The set page offers no Publish, Add, Edit or Withdraw, and a profile of the project's own lists its
    objectives instead of the picker."""
    http, projects = gated
    pid, _, subject = project_member("viewer")
    library = projects.library
    made = library.create_set("BNK", "Bank policies", "ours", who="owner")
    profile = library.create_profile("Bank", "", ["O1", "O2"], who="owner")
    headers = {"Authorization": f"Bearer {signing(subject)}"}
    for path in (f"/sets/{made.id}", f"/profiles/{profile.id}"):
        r = http.get(f"/p/{pid}{path}", headers=headers)
        assert r.status_code == 200, r.text
        assert _posting_forms(r.text) == [], f"a viewer is offered forms on {path}"
        assert re.search(r"can read this project but not change it", _shown(r.text), re.I), path
    shown = _shown(http.get(f"/p/{pid}/profiles/{profile.id}", headers=headers).text)
    assert "O1" in shown and "O2" in shown, "the viewer does not see the profile's objectives"
