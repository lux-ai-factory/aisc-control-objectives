---
dimension: transparency
applies_when: "system produces outputs consumed by people or downstream systems"
---
Transparency (Articles 13 and 50) covers both documentation for deployers and
disclosure to affected people.

Adequate coverage requires evidence of:
- **Instructions for use (IFU)** that state capabilities, limitations, intended
  purpose, accuracy levels, and human-oversight measures — and, where downstream
  systems consume the output, a **machine-readable** counterpart (JSON/XML), not
  only prose.
- A **model card** or equivalent (data, evaluation, known limitations).
- **AI-interaction disclosure**: where a person interacts with the system or
  consumes generated content, that it is AI-generated is disclosed; synthetic
  media is marked.
- **Explanation** of individual decisions where they affect a person.

A human-readable IFU with no machine-readable form, on a system whose output
feeds another system, is a residual gap — not full coverage.
