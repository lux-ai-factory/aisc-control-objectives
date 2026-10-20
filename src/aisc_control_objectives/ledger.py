"""This service's ledger events.

Events are built and checked by aisc_identity.ledger_events, which also keeps the
witnessed request id (RequestId) that every event cites. emit() writes one inside
the transaction of the change it describes (the repository's or the library's
`sessionmaker.begin()` block).

An AI mapping happens inside the request that asks for it. Its request (`ai.mapping.requested`) is written
first, in a transaction of its own, before the first model call; the run's own events (each `ai.llm_call`,
then `ai.mapping.completed` or `ai.mapping.failed`) follow in the save's transaction, under the same run id.
"""
from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable

from aisc_identity.ledger_events import NotLedgerSafe, RequestId, current_request, event, on
from sqlalchemy import text

__all__ = ["Calls", "NotLedgerSafe", "RequestId", "current_request", "emit", "on"]


def emit(session, action: str, **fields) -> str | None:
    """Queue one event on `session`, inside its open transaction. None while the ledger is off."""
    if not on():
        return None
    body = event(action, **fields)
    session.execute(text("SELECT ledger.emit(CAST(:e AS jsonb))"), {"e": json.dumps(body, allow_nan=False)})
    return body["event_id"]


class Calls:
    """The model calls of one AI mapping, for `ai.llm_call` events (written later, in the save's transaction).
    Each call names the risk it was for and its round on that risk."""

    def __init__(self):
        self.calls: list[dict] = []
        self._risk: str | None = None
        self._rounds: dict[str, int] = {}

    def recording(self, complete: Callable[..., str], purpose: str) -> Callable[..., str]:
        def recorded(system: str, user: str, *args, **kwargs) -> str:
            started = time.monotonic()
            try:
                return complete(system, user, *args, **kwargs)
            finally:
                self.calls.append({"purpose": purpose, "property": self._risk,
                                   "round": self._rounds.get(self._risk) if self._risk else None,
                                   "latency_ms": int((time.monotonic() - started) * 1000)})
        return recorded

    def watching(self, mapper):
        """A copy of `mapper` whose model calls are recorded, each with its risk and round. A mapper that
        calls no model (`_complete`) is returned as it is."""
        if not hasattr(mapper, "_complete"):
            return mapper
        watched = copy.copy(mapper)
        watched._complete = self.recording(mapper._complete, "mapping")
        propose = watched.propose                                     # bound to the copy, so its calls are recorded

        def proposing(risk, *args, **kwargs):
            self._risk = risk.id
            self._rounds[risk.id] = self._rounds.get(risk.id, 0) + 1
            return propose(risk, *args, **kwargs)
        watched.propose = proposing
        return watched

    def emit_all(self, session, run_id: str, model: str | None) -> None:
        for n, call in enumerate(self.calls, 1):
            details = {"purpose": call["purpose"], "latency_ms": call["latency_ms"]}
            if call["property"] is not None:
                details.update(property=call["property"], round=call["round"])
            emit(session, "ai.llm_call", item_type="llm_call", item_id=f"{run_id}:{n}", run_id=run_id, model=model,
                 details=details)
