---
sectors:
  - finance-and-insurance
  - health
  - education
  - public-sector
  - defence
  - social-&-welfare-issues
---
# High-risk sectors — EU AI Act Annex III mapping

This document is the source of truth for the **D2 deterministic floor**: when a
system's sector is listed here, a trustworthiness dimension cannot be
auto-marked "covered" without an accepted control checklist. It is domain
knowledge — maintained by a compliance reviewer, not an operator — so it lives
in the document base rather than in `wizard.toml`. The `[floor] enabled` toggle
in `wizard.toml` only turns the mechanism on or off.

`sectors` lists **catalogue sector slugs** (the slugs the catalogue actually
tags tools and system cards with). Each maps to an EU AI Act Annex III
high-risk area:

| Catalogue sector slug      | Annex III high-risk area |
|----------------------------|--------------------------|
| `finance-and-insurance`    | Creditworthiness / credit scoring; risk assessment & pricing in life and health insurance (Annex III §5(b)). |
| `health`                   | Access to essential healthcare services; safety components (Annex III §5(a)). |
| `education`                | Education and vocational training — admission, assessment, proctoring (Annex III §3). |
| `public-sector`            | Access to essential public services and benefits; law enforcement; migration/asylum; administration of justice (Annex III §5(a), §6–8). |
| `defence`                  | National-security / defence use — treated as high-risk for assessment rigour here even where carve-outs may apply. |
| `social-&-welfare-issues`  | Eligibility for public assistance benefits and services (Annex III §5(a)). |

Horizontal, non-high-risk sectors (e.g. `digital-economy`,
`industry-&-entrepreneurship`, `science-&-technology`) are deliberately **not**
listed: their presence alone does not raise the floor.

To change the policy, edit the `sectors` list above using catalogue slugs. Keep
the slugs in sync with the catalogue's sector vocabulary, or the floor will
silently never trigger for a misspelled entry.
