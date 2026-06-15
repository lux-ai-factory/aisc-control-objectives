---
dimension: technical-robustness-and-safety
ai_types: [natural-language-processing, agents-and-agentic-systems]
sectors: [finance-and-insurance]
applies_when: "system exposes an LLM endpoint to untrusted input"
---
For LLM and agentic systems, robustness testing must include prompt-injection
and jailbreak resistance, not just accuracy under benign input. Treat any tool
output reaching the model as untrusted. Adequate coverage requires an adversarial
benchmark (e.g. injection success rate) in addition to task-utility metrics.
