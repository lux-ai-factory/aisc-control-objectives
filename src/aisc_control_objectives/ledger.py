"""This service's ledger events (docs/superpowers/ledger-2026-10-02/02-spec.md 6.4, 6.5; phase 6).

An event is written with the project database's `ledger.emit(jsonb)` inside the SAME transaction as the
change it describes (the repository's or the library's `sessionmaker.begin()` block), so a rollback
leaves no event and a committed change always has one (R2.4). The service never names who acted: it
cites the request the gateway witnessed (`X-AISC-Request-Id`, kept per request by `RequestId`), and the
platform's relay takes the person from that record. It sends plain content; the platform computes the
keyed digests (N4). Nothing is written while LEDGER_MODE is off (the default).

An AI mapping happens inside the request that asks for it, so its run (`ai.mapping.requested`, each
`ai.llm_call`, then `ai.mapping.completed` or `ai.mapping.failed`) is written together, in the save's
transaction, under one run id.
"""
from __future__ import annotations

import contextvars
import json
import math
import os
import time
import uuid
from typing import Any, Callable

from sqlalchemy import text

#: The witnessed request being served, or None outside one.
current_request: contextvars.ContextVar[str | None] = contextvars.ContextVar("ledger_request", default=None)

_MAX_SAFE = 2 ** 53


class NotLedgerSafe(ValueError):
    """A value the ledger can't keep (an integer beyond 2**53, NaN, an infinity, a lone surrogate)."""


def on() -> bool:
    return os.environ.get("LEDGER_MODE", "off").strip().lower() in ("record", "enforce")


def _check(value: Any) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE:
            raise NotLedgerSafe(f"integer beyond 2**53: {value}")
    elif isinstance(value, float):
        if not math.isfinite(value) or (value.is_integer() and abs(value) > _MAX_SAFE):
            raise NotLedgerSafe(f"a number the ledger can't keep: {value}")
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise NotLedgerSafe("a lone surrogate has no UTF-8 form") from None
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            _check(k)
            _check(v)
    else:
        raise NotLedgerSafe(f"JSON has no form for {type(value).__name__}")


def body(action: str, *, item_type: str, item_id: str | None, details: dict | None = None, content=None,
         before=None, after=None, run_id: str | None = None, model: str | None = None,
         item_version: str | None = None, card_version: str | None = None) -> dict:
    """The event as `ledger.emit` takes it; NotLedgerSafe for a value the relay would reject later."""
    event: dict[str, Any] = {"event_id": str(uuid.uuid4()), "request_id": current_request.get(),
                             "action": action, "item_type": item_type, "item_id": item_id,
                             "details": details or {}}
    for key, value in (("content", content), ("before", before), ("after", after), ("run_id", run_id),
                       ("model", model), ("item_version", item_version), ("card_version", card_version)):
        if value is not None:
            event[key] = value
    _check(event)
    return event


def emit(session, action: str, **fields) -> str | None:
    """Queue one event on `session`, inside its open transaction. None while the ledger is off."""
    if not on():
        return None
    event = body(action, **fields)
    session.execute(text("SELECT ledger.emit(CAST(:e AS jsonb))"), {"e": json.dumps(event, allow_nan=False)})
    return event["event_id"]


class Calls:
    """The model calls of one AI mapping, for `ai.llm_call` events (written later, in the save's transaction)."""

    def __init__(self):
        self.calls: list[dict] = []

    def recording(self, complete: Callable[..., str], purpose: str) -> Callable[..., str]:
        def recorded(system: str, user: str, *args, **kwargs) -> str:
            started = time.monotonic()
            try:
                return complete(system, user, *args, **kwargs)
            finally:
                self.calls.append({"purpose": purpose, "latency_ms": int((time.monotonic() - started) * 1000)})
        return recorded

    def emit_all(self, session, run_id: str, model: str | None) -> None:
        for n, call in enumerate(self.calls, 1):
            emit(session, "ai.llm_call", item_type="llm_call", item_id=f"{run_id}:{n}", run_id=run_id, model=model,
                 details={"purpose": call["purpose"], "round": n, "latency_ms": call["latency_ms"]})


class RequestId:
    """ASGI middleware: the request the gateway witnessed (X-AISC-Request-Id), for this request's events."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        presented = dict(scope.get("headers") or []).get(b"x-aisc-request-id")
        value = None
        if presented:
            try:
                value = str(uuid.UUID(presented.decode("latin-1")))
            except ValueError:
                value = None                                          # never put anything else in an event
        token = current_request.set(value)
        try:
            return await self.app(scope, receive, send)
        finally:
            current_request.reset(token)
