---
dimension: technical-robustness-safety
ai_types: [natural-language-processing, agents-and-agentic-systems, knowledge-and-retrieval]
applies_when: "system exposes an LLM endpoint or lets a model act through tools"
---
For LLM, RAG, and agentic systems, robustness under EU AI Act Article 15 is not
covered by task-accuracy metrics alone. Treat any text that reaches the model —
user input, retrieved documents, tool outputs — as untrusted.

Adequate coverage requires evidence of:
- **Prompt-injection and jailbreak resistance** measured with an adversarial
  benchmark (e.g. injection success rate / attack success rate), not just benign
  utility. For agentic systems, test injections embedded in tool outputs.
- **Jailbreak / safety-bypass** rates against a known attack suite.
- For RAG: robustness to poisoned or contradictory retrieved context, and
  groundedness of answers (no fabrication when the context is insufficient).

Raise priority to "must" when the system is high-risk or acts with real-world
side effects (transactions, messaging, file or account access). A robustness
claim with only clean-input accuracy is a residual gap, not coverage.
