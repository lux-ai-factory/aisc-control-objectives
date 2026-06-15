---
dimension: human-agency-oversight
ai_types: [agents-and-agentic-systems, predictive-and-analytical-ai, tabular-and-structured-data]
applies_when: "system automates or strongly informs a decision affecting a person"
---
Human agency and oversight (Article 14) requires that a competent person can
understand, intervene in, and stop the system in operation.

Adequate coverage requires evidence of:
- **Effective oversight measures**: the operator can interpret the output
  (see the transparency dimension), override it, and is not subject to
  automation bias by design.
- A working **stop / kill-switch** and a defined safe state on shutdown — and
  evidence it was tested, not just specified.
- For agentic systems: **human-in-the-loop or human-on-the-loop gating** of
  high-consequence actions, and bounded autonomy (the agent cannot exceed its
  intended scope of action).
- Clear **escalation / fallback** to a human path.

A documented kill-switch with no test evidence, or an agent able to take
irreversible actions without a confirmation gate, is a residual gap. Raise to
"must" for high-risk deployments.
