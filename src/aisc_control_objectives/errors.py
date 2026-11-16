"""An error as a reader may see it: `shown_error(text, limit)` (a mapping run's stored error).

A model client's exception can quote the request URL with its `?key=`, an Authorization header,
the provider's JSON echo of a key, or the provider's message about the key. So the text a reader
sees is cut first, cleared of anything shaped like a credential, then cut to `limit`; the full text
goes only to the service's log.

The rule is aisc_redact.shown_error, shared by every Python service and held to the shared vectors
redaction-cases.json; the qualification agents, which cannot import it, keep a copy that runs the
same vectors (apps/qualification/services/agents/fill/errors.py).
"""
from __future__ import annotations

from aisc_redact import REDACTED, shown_error  # noqa: F401 (the service's names for the shared rule)
