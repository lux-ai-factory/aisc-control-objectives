"""The skills on disk: what each model-facing step is told to do.

Markdown rather than Python strings, so how a step behaves can be changed
without touching code, which is the same reason the qualification app's filler
keeps its skills as files. Frontmatter (BAF's format: name, description) is
stripped, because it addresses the reader, not the model.

Its own module because both model-facing steps need it, and having one of them
import it from the other made a sibling look like a dependency of its twin.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parent / "skills"


def load_skill(name: str) -> str:
    """One skill's body, frontmatter stripped."""
    path = SKILLS / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"skill {name!r} not found at {path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL).strip()
