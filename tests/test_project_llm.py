"""The risk mapper works with the model its project chose (01-specs.md S3.9, S3.10; D8).

`Projects(..., mapper_for=fn)` asks `fn(record.project)` for a (Mapper, model label) at each
map; `server.build_app` wires it to `baf_llm.config_for(pid, "risk_mapper", fallback=<the
startup config>)`. Runs on the throwaway test database (CONTROL_OBJECTIVES_TEST_DATABASE_URL);
the platform and the model endpoint are fake servers on 127.0.0.1.
"""
from __future__ import annotations

import importlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping
from fake_http import Canned, FakeServer, chat_completion, closed_port_url, new_key

TOKEN = "pytest-internal-" + uuid.uuid4().hex


class FakeMapper:
    def __init__(self, objective="O1"):
        self.objective, self.calls = objective, 0

    def propose(self, risk, findings=()):
        self.calls += 1
        return Mapping(risk_id=risk.id, objectives=[
            MappedObjective(objective_id=self.objective, quote=risk.text[:30], rationale="because")])


@pytest.fixture
def baf_llm():
    return importlib.import_module("aisc_control_objectives.baf_llm")


@pytest.fixture
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


@pytest.fixture
def assessment(graph, platform_project, system_version):
    def make(projects):
        view = projects.create(platform_project, "MCAS", json.dumps(graph), graph,
                               system_version(platform_project, 1))
        return view.record.id
    return make


# ── S3.9 Projects asks for the project's mapper ──────────────────────────────


def test_s3_9_map_uses_the_mapper_of_the_records_project_and_records_its_label(
        repository, objectives, assessment, platform_project):
    fixed, chosen, asked = FakeMapper("O1"), FakeMapper("O21"), []

    def mapper_for(pid):
        asked.append(pid)
        return chosen, "compatible/project-model"

    projects = Projects(repository, objectives, fixed, model="env/startup", mapper_for=mapper_for)
    view = projects.map_risks_of(assessment(projects))
    assert asked == [platform_project]
    assert chosen.calls > 0 and fixed.calls == 0
    assert view.record.mapping_run.model == "compatible/project-model"


def test_s3_9_d8_without_mapper_for_the_fixed_mapper_and_label_are_used(repository, objectives, assessment):
    fixed = FakeMapper()
    projects = Projects(repository, objectives, fixed, model="env/startup")
    view = projects.map_risks_of(assessment(projects))
    assert fixed.calls > 0
    assert view.record.mapping_run.model == "env/startup"


# ── S3.10 a failure to get the model is a 502, and saves nothing ─────────────


def _failing(exc):
    def mapper_for(pid):
        raise exc
    return mapper_for


@pytest.fixture
def failing_client(repository, objectives, baf_llm):
    def make(exc):
        projects = Projects(repository, objectives, FakeMapper(), model="env/startup", mapper_for=_failing(exc))
        return projects, TestClient(create_app(objectives, projects, base_config=RunConfig()))
    return make


@pytest.mark.parametrize("kind", ["resolve", "build"])
def test_s3_10_the_api_answers_502_with_the_reason_and_saves_nothing(failing_client, baf_llm, assessment, kind,
                                                                     platform_project):
    key = new_key()
    message = ("the platform could not resolve the risk mapper's model for project x: the stored key for "
               "openai cannot be decrypted; enter it again") if kind == "resolve" else \
        "openai needs OPENAI_API_KEY in the environment"
    exc = baf_llm.ResolveError(message) if kind == "resolve" else ValueError(message)
    projects, client = failing_client(exc)
    pid = assessment(projects)
    r = client.post(f"/p/{platform_project}/api/projects/{pid}/map")
    assert r.status_code == 502, r.text
    assert r.json() == {"detail": message}
    assert key not in r.text
    assert client.get(f"/p/{platform_project}/api/projects/{pid}").json()["mapping_run"] is None


def test_s3_10_the_form_route_answers_502_plain_text_and_saves_nothing(failing_client, baf_llm, assessment,
                                                                        platform_project):
    message = "the platform did not answer"
    projects, client = failing_client(baf_llm.ResolveError(message))
    pid = assessment(projects)
    r = client.post(f"/p/{platform_project}/projects/{pid}/map", follow_redirects=False)
    assert r.status_code == 502
    assert r.headers["content-type"].startswith("text/plain")
    assert message in r.text
    assert client.get(f"/p/{platform_project}/api/projects/{pid}").json()["mapping_run"] is None


# ── S3.9 server.build_app wires mapper_for to the platform ───────────────────


@pytest.fixture
def platform():
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture
def model_server():
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture
def built(monkeypatch, tmp_path, database_url, platform):
    """build_app with an ollama startup config (no network at startup) and the fake platform;
    returns the keyword arguments build_app gave Projects."""
    from aisc_control_objectives import server

    for name in ("OPENAI_API_KEY", "MISTRAL_API_KEY", "BAF_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("CONTROL_OBJECTIVES_CONFIG_FILE", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("BAF_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BAF_LLM_MODEL", "startup-model")
    monkeypatch.setenv("BAF_LLM_BASE_URL", closed_port_url())
    monkeypatch.setenv("PLATFORM_URL", platform.url)
    monkeypatch.setenv("PLATFORM_INTERNAL_TOKEN", TOKEN)
    captured = {}

    class Capturing(Projects):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(server, "Projects", Capturing)
    server.build_app()
    return captured


def test_s3_9_d8_build_app_keeps_the_startup_model_label(built):
    assert built.get("model") == "ollama/startup-model"
    assert callable(built.get("mapper_for")), "build_app does not give Projects a mapper_for"


def test_s3_9_no_choice_on_the_platform_falls_back_to_the_startup_config(built, platform):
    pid = str(uuid.uuid4())
    platform.reply(f"/internal/projects/{pid}/llm/risk_mapper", {"configured": False})
    _mapper, label = built["mapper_for"](pid)
    assert label == "ollama/startup-model"
    (seen,) = platform.seen(f"/internal/projects/{pid}/llm/risk_mapper")
    assert seen.header("X-AISC-Service-Token") == TOKEN


def test_s3_9_s3_11_the_projects_choice_reaches_the_model(built, platform, model_server, mcas_graph):
    pid, key = str(uuid.uuid4()), new_key()
    risk = mcas_graph.risks[0]
    model_server.reply("/v1/chat/completions", lambda seen: Canned(200, json.dumps(chat_completion(
        json.dumps({"risk_id": risk.id, "objectives": []}))).encode()), method="POST")
    platform.reply(f"/internal/projects/{pid}/llm/risk_mapper", {
        "configured": True, "provider": "compatible", "model": "project-model",
        "base_url": model_server.url + "/v1", "api_key": key})
    mapper, label = built["mapper_for"](pid)
    assert label == "compatible/project-model"
    mapping = mapper.propose(risk)
    assert mapping.risk_id == risk.id
    (sent,) = model_server.seen("/v1/chat/completions")
    assert sent.header("Authorization") == f"Bearer {key}"
    assert json.loads(sent.body)["model"] == "project-model"


def test_s3_9_d4_a_failing_platform_is_not_replaced_by_the_startup_model(built, platform, baf_llm):
    pid = str(uuid.uuid4())
    platform.reply(f"/internal/projects/{pid}/llm/risk_mapper", {"detail": "nope"}, status=503)
    with pytest.raises(baf_llm.ResolveError):
        built["mapper_for"](pid)
