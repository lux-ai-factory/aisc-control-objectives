---
dimension: record-keeping
applies_when: "high-risk system that must be auditable after the fact"
---
Record-keeping (Article 12) is a governance dimension satisfied by logging
controls, not test tools.

Adequate coverage requires evidence of:
- **Automatic logging** of events over the system's lifetime sufficient to
  reconstruct its operation and trace incidents (inputs/decisions at the
  granularity the use case needs, timestamps, model/version).
- **Retention and deletion** policies consistent with the privacy dimension.
- **Traceability** linking a given output back to the data, model version, and
  configuration that produced it.

This dimension has controls but no tests; the deterministic floor prevents it
being auto-marked "covered" without an accepted record-keeping control.
