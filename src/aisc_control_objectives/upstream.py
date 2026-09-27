"""The two services an assessment is started from.

The platform says which AI card version is the project's latest; qualification
serves that version's card as JSON-LD. Both are called with the caller's own
token, as `Authorization: Bearer` and as the gateway's X-Auth-Request-Access-Token
(the handler takes it from either incoming header): this service has no account of its
own, and a project belongs to the people in it. Any answer other than the ones
named below, or no answer, is `UpstreamDown`.
"""

from __future__ import annotations

import os

import httpx

from aisc_identity.headers import GATEWAY_TOKEN_HEADER

TIMEOUT = 10.0


class UpstreamDown(Exception):
    """The platform or qualification did not answer as expected."""


def _base(variable: str) -> str:
    base = (os.environ.get(variable) or "").rstrip("/")
    if not base:
        raise UpstreamDown(f"{variable} is not set")
    return base


def _headers(authorization: str | None) -> dict[str, str]:
    """The caller's token, both ways the other services read one.

    Bearer is what the platform and qualification check first; the gateway's
    own header is sent too, so a callee that only reads what the gateway
    delivers still sees the same caller.
    """
    if not authorization:
        return {}
    headers = {"Authorization": authorization}
    if authorization.lower().startswith("bearer "):
        token = authorization[len("bearer "):].strip()
        if token:
            headers[GATEWAY_TOKEN_HEADER] = token
    return headers


def _get(url: str, authorization: str | None, service: str) -> httpx.Response:
    """GET `url` as the caller; `service` is who the error names when nothing answers."""
    try:
        return httpx.get(url, headers=_headers(authorization), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise UpstreamDown(f"{service} did not answer: {exc}") from exc


def latest_version(project: str, authorization: str | None) -> dict | None:
    """The project's latest card version (a row of the project database's project.system), or None when it has none."""
    url = f"{_base('PLATFORM_URL')}/projects/{project}/system-versions/latest"
    response = _get(url, authorization, "the platform")
    if response.status_code != 200:
        raise UpstreamDown(f"the platform answered {response.status_code}")
    return response.json()


def card_jsonld(project: str, system_pid: str, authorization: str | None) -> str | None:
    """The card of one version as the bytes qualification serves; None when it has no card."""
    url = (
        f"{_base('QUALIFICATION_URL')}/p/{project}/api/system-versions/{system_pid}/ontology.jsonld"
    )
    response = _get(url, authorization, "qualification")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise UpstreamDown(f"qualification answered {response.status_code}")
    return response.content.decode("utf-8")
