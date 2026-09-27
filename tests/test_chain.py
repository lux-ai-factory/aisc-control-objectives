"""Control objectives' step of the pipeline chain (scripts/test-pipeline-chain.sh, 03 WP12).

Skipped unless CHAIN_JSON names the chain's shared state. Step 3: an assessment of
card version 1, started the way the page starts one (no file), with the platform
and qualification stubbed by a local HTTP server. The card served is the MCAS
fixture plus one node per card_component row of the chain's card, read from the
chain's database, so the step consumes two links: the card's component rows, and
the assessment's key into core.system.

The repository is built directly on the chain's database: the `repository`
fixture drops and re-creates the database it is given, which here is `platform`
(F14b).
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

CHAIN_JSON = os.environ.get("CHAIN_JSON")
pytestmark = [
    pytest.mark.chain,
    pytest.mark.skipif(not CHAIN_JSON, reason="not in the pipeline chain (CHAIN_JSON unset)"),
]

FIXTURES = Path(__file__).parent / "fixtures"
AUTH = "Bearer chain-caller"


def _serve(latest: dict, card: str):
    latest_path = re.compile(r"^/projects/[^/]+/system-versions/latest$")
    card_path = re.compile(r"^/qualification/p/[^/]+/api/system-versions/([^/]+)/ontology\.jsonld$")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if latest_path.match(path):
                body, kind = json.dumps(latest).encode(), "application/json"
            elif (found := card_path.match(path)) and found.group(1) == latest["pid"]:
                body, kind = card.encode("utf-8"), "application/ld+json"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_chain_step3_assessment_on_v1(monkeypatch):
    from aisc_control_objectives.api.app import create_app
    from aisc_control_objectives.control_objectives import load_control_objectives
    from aisc_control_objectives.db.repository import ProjectRepository
    from aisc_control_objectives.projects import Projects
    from aisc_control_objectives.risk_mapping import Mapping

    state = json.loads(Path(CHAIN_JSON).read_text())
    with psycopg.connect(os.environ["CHAIN_SU_DSN"]) as conn:
        components = conn.execute(
            "SELECT component_pid::text, airo_property, name, object_name"
            "  FROM qualification.card_component WHERE qualification_id = %s ORDER BY name",
            (state["card_v1_id"],),
        ).fetchall()
        co_fk = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'fk_project_system_id_core_system'"
            " AND conrelid = 'control_objectives.project'::regclass"
        ).fetchone()
    assert len(components) == 2, "the card of v1 links two engine components"
    assert co_fk is not None, "an assessment's key into core.system"

    graph = json.loads((FIXTURES / "mcas.ontology.jsonld").read_text())
    for pid, prop, name, object_name in components:
        graph.append({
            "@id": f"urn:aisc:component:{pid}",
            "@type": ["https://w3id.org/airo#AIModel" if prop == "hasModel" else "https://w3id.org/airo#Data"],
            "http://www.w3.org/2000/01/rdf-schema#label": [{"@value": name}],
            "https://lux-ai-factory.github.io/qualification/ns#objectName": [{"@value": object_name}],
        })
    latest = {"pid": state["v1_pid"], "project_id": state["project_pid"], "number": 1}
    server, url = _serve(latest, json.dumps(graph))
    monkeypatch.setenv("PLATFORM_URL", url)
    monkeypatch.setenv("QUALIFICATION_URL", url + "/qualification")
    try:
        objectives = load_control_objectives()
        repository = ProjectRepository(os.environ["CONTROL_OBJECTIVES_TEST_DATABASE_URL"],
                                       objectives_digest=objectives.digest)

        class NoMapper:
            def propose(self, risk, findings=()):
                return Mapping(risk_id=risk.id)

        client = TestClient(create_app(objectives, Projects(repository, objectives, NoMapper())),
                            follow_redirects=False, raise_server_exceptions=False)
        started = client.post(f"/p/{state['project_pid']}/projects", data={"name": "MCAS"},
                              headers={"Authorization": AUTH})
        assert started.status_code == 303, (started.status_code, started.text[:300])
        assessment = started.headers["location"].rsplit("/", 1)[-1]
        record = repository.get(assessment)
        assert record.system_id == state["v1_pid"]
    finally:
        server.shutdown()

    state = json.loads(Path(CHAIN_JSON).read_text())
    state["assessment_v1_id"] = assessment
    Path(CHAIN_JSON).write_text(json.dumps(state))
