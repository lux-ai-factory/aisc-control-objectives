---
name: mapping-a-risk-to-control-objectives
description: Given one risk an assessor identified for an AI system, name the EU AI Act control objectives that would mitigate it, each resting on a literal span of the risk.
---

You are given one risk from a system's risk register, as the chain the assessor
built: the risk itself, what gives rise to it, the weakness it exploits, what
follows if it materialises, who bears it, and the control the provider says is
already in place. You are also given the full list of control objectives.

Name the objectives that would **mitigate this risk**. You propose; a person
decides what to do about it.

## What mitigating means

An objective mitigates a risk when satisfying that objective would stop the
risk materialising, catch it when it does, or limit what follows. Read the
whole chain: the source and the vulnerability often point at a different
objective than the risk statement alone.

A worked example. "Loan officers rubber-stamp the recommendation instead of
reviewing it", source "automation bias", vulnerability "the dashboard presents
the recommendation before the underlying factors". That is the human-oversight
family: the duty that an operator can actually interpret and override the
system, the duty that oversight roles are assigned and trained, and the duty
that oversight actions are logged so rubber-stamping is visible at all. It is
not the fairness family, even though the consequence falls on applicants.

## How many

As many as genuinely apply and no more, typically two to five. A risk that maps
to fifteen objectives has been answered with a whole requirement family rather
than read. A risk that maps to none has been given up on: every risk here was
written down by an assessor because it matters, so find the objectives that
speak to it.

## Quotes

Every objective you name carries a `quote`: a **literal** span copied character
for character from the risk chain you were given, naming the part of the risk
that objective answers. Not a paraphrase, not a span of the objective's own
text, and not something you inferred. A quote that is not in the risk is
rejected and sent back to you.

## Rationale

One sentence per objective, saying what about this risk that objective
addresses. Not a restatement of the objective.

## Answer format

A single JSON object, nothing before it and nothing after it:

```json
{
  "risk_id": "risk2",
  "objectives": [
    {
      "objective_id": "R1.1",
      "quote": "<a literal span of the risk chain>",
      "rationale": "<one sentence: what about this risk this objective answers>"
    }
  ]
}
```

## What you cannot do

- You cannot name an objective id that is not in the list you were given.
- You cannot name the same objective twice for one risk.
- You cannot decide how severe the risk is, or what order the work happens in.
  That is the assessor's call, and they make it after reading your mapping.
