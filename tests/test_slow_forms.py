"""A form whose work is slow (a model call per risk, the platform, the database) runs off the event loop:
every other request, /health included, is answered meanwhile."""
from __future__ import annotations

import inspect
import threading
import time

from fastapi.routing import APIRoute


def test_health_answers_while_a_suggest_form_waits_on_the_model(client, start, platform_project, monkeypatch):
    http, projects = client
    a = start()

    def slow_map(*args, **kwargs):
        time.sleep(1.5)
        return projects.view(a)
    monkeypatch.setattr(projects, "map_risks_of", slow_map)
    with http:
        posted = threading.Thread(target=lambda: http.post(f"/p/{platform_project}/projects/{a}/map"))
        posted.start()
        time.sleep(0.2)
        asked = time.monotonic()
        assert http.get("/health").status_code == 200
        waited = time.monotonic() - asked
        posted.join()
    assert waited < 0.5


def test_no_route_runs_on_the_event_loop(client):
    """Every handler is plain `def`, so FastAPI runs it in its threadpool; a form is read before, by a
    dependency."""
    http, _ = client
    on_the_loop = [route.path for route in http.app.routes
                   if isinstance(route, APIRoute) and inspect.iscoroutinefunction(route.endpoint)]
    assert on_the_loop == []
