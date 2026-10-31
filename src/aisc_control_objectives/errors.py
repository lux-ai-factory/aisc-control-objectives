"""An error as a reader may see it: `shown_error(text, limit)`.

Two byte-identical copies, one per app, since neither app imports the other:
apps/qualification/services/agents/fill/errors.py (a fill run's failure, read through the web
app's GET /p/{pid}/api/qualifications/{id}/fill by the project's editors and owners only) and
apps/control-objectives/src/aisc_control_objectives/errors.py (a mapping run's stored error).
Change both together.

A model client's exception can quote the request URL with its `?key=`, an Authorization header,
the provider's JSON echo of a key, or the provider's message about the key. So the text a reader
sees is cut first (the patterns are super-quadratic on long input), cleared of anything shaped like
a credential, then cut to `limit`; the full text goes only to the service's log.

The first set of rules is the worker's text rule (apps/eval/aisc_eval/scrub.py `scrub_text`, without
its known values): key shapes, `Authorization: Token ...`, and the key-like value of a `password=...`
or JSON `"api_key": "..."` assignment. The second set is the older, blunter rule this text had
before, kept so nothing it took out comes back: any `key=` or `token:` value, a short `sk-` key,
any long run of letters and digits.

Stdlib only.
"""
from __future__ import annotations

import re

REDACTED = "[redacted]"

# The worker's text rule (aisc_eval/scrub.py), copied: see there for what each part catches and misses.
_CREDENTIAL = r"[A-Za-z0-9+/=_.~-]{16,}"
_SECRET_SHAPE = re.compile(
    r"(?i)\bbearer\s+" + _CREDENTIAL + r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*"
    r"|\bhf_[A-Za-z0-9]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|(\bauthorization\s*:\s*)?\b(?:basic|token)\s+"
    + _CREDENTIAL
    + r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:[a-z0-9-]*_|[a-z0-9]*(?-i:[a-z0-9])(?=(?-i:[A-Z])))?"
    r"(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret[_-]?key"
    r"|(?:aws[_-]?)?secret[_-]?access[_-]?key|client[_-]?secret|password|passwd"
    r"|(?:token|pwd)(?=[\"']?\s*(?:=|%3d))))"
    r"([\"']?\s*(?:[=:]|%3d)\s*[\"']?)"
    r"((?!\[redacted\])(?:(?<=\")[^\"\s]+(?=\")|(?<=')[^'\s]+(?=')|[^\s\"',;]+))"
)
_CREDENTIAL_VALUE = re.compile(_CREDENTIAL)
_STRONG_TOKEN_NAME = re.compile(r"(?i)(?:access|auth|refresh)[_-]?token$")

# The older rule.
_KEY_LIKE = (
    # `Bearer <token>`, `Basic <credentials>`
    (re.compile(r"(?i)\b(bearer|basic)\s+(?!\[redacted\])[^\s,;]+"), r"\1 " + REDACTED),
    # `key=...`, `api_key: ...`, `token=...`, `password=...` and the like
    (re.compile(r"(?i)\b([a-z_-]*(?:key|token|secret|password|passwd|pwd|signature|sig|auth|authorization"
                r"|credentials?))(\s*[=:]\s*)(?!\[redacted\])[^\s&,;]+"),
     r"\1\2" + REDACTED),
    # `scheme://user:password@host`
    (re.compile(r"://(?!\[redacted\]@)[^/\s:@]+:[^/\s@]+@"), "://" + REDACTED + "@"),
    # the providers' own key shapes: sk-..., xai-..., gsk_..., hf_..., AIza...
    (re.compile(r"\b(?:sk|pk|rk|xai|gsk|hf|ghp|glpat)[-_][A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{10,}"), REDACTED),
    # any other long run of letters and digits together
    (re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}"), REDACTED),
)


def _key_like(name: str, value: str) -> bool:
    """Whether an assigned value looks like a key rather than a word (as aisc_eval/scrub.py decides)."""
    if name.lower().endswith("token") and not _STRONG_TOKEN_NAME.search(name):
        return _CREDENTIAL_VALUE.fullmatch(value) is not None
    if any(char.isdigit() for char in value):
        return True
    return len(value) >= 16 and value.lower() != value and value.upper() != value


def _redact_shape(match: re.Match) -> str:
    return (match.group(1) or "") + REDACTED


def _redact_assignment(match: re.Match) -> str:
    if not _key_like(match.group(1), match.group(3)):
        return match.group(0)
    return match.group(1) + match.group(2) + REDACTED


def shown_error(text: str, limit: int) -> str:
    """`text` as a reader may see it: on one line, key-like parts taken out, at most `limit` characters.

    The text is cut to 4 x `limit` before it is scrubbed, since nothing past the final cut is shown.
    A word that first cut splits is dropped whole, so no half of a key escapes its pattern."""
    first, text = 4 * limit, text or ""
    out = re.sub(r"\s+", " ", text[:first]).strip()
    if len(text) > first and " " in out:
        out = out.rsplit(" ", 1)[0]
    out = _SECRET_SHAPE.sub(_redact_shape, out)
    out = _SECRET_ASSIGNMENT.sub(_redact_assignment, out)
    for pattern, replacement in _KEY_LIKE:
        out = pattern.sub(replacement, out)
    return out if len(out) <= limit else out[: limit - 3].rstrip() + "..."
