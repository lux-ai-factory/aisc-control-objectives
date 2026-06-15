"""Parse a `---`-delimited YAML frontmatter header from a document.

Shared by every reader of the document base (skills, knowledge references), so
the header format is defined once.
"""

from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata, body) for a Markdown document with an optional
    `---`-fenced YAML header. A document with no header yields ({}, whole-text);
    a header that isn't a YAML mapping yields ({}, body)."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].strip()
