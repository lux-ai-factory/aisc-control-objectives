"""Extract EU AI Act article/annex references, normalized to article-level keys.

Matching happens at the article level: "Article 13.2" (system card) must
overlap "Article 13" (checklist question), so sub-references collapse to the
parent article. Annexes are referenced by roman numeral.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# "Article 13", "Article 13.2", "Articles 10, 12, 13, and 14"
_ARTICLE = re.compile(r"\barticles?\s+(\d+(?:\s*,\s*(?:and\s+)?\d+)*(?:\s*,?\s*and\s+\d+)?)", re.I)
# "Annex IV", "Annex IV.1.a", "annex iii"
_ANNEX = re.compile(r"\bannex\s+([ivxlc]+)\b", re.I)


def article_keys(text: str | Iterable[str] | None) -> set[str]:
    """Return normalized reference keys ("article-13", "annex-iv") found in text.

    Accepts a single string, an iterable of strings, or None.
    """
    if text is None:
        return set()
    if not isinstance(text, str):
        keys: set[str] = set()
        for part in text:
            keys |= article_keys(part)
        return keys

    keys = set()
    for match in _ARTICLE.finditer(text):
        for number in re.findall(r"\d+", match.group(1)):
            keys.add(f"article-{number}")
    for match in _ANNEX.finditer(text):
        keys.add(f"annex-{match.group(1).lower()}")
    return keys
