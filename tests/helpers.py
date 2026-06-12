"""Shared test doubles and factories (one canonical copy of each).

pytest puts this directory on sys.path, so test files do `import helpers`.
"""

from wizard.models.plan import ItemVerdict, Proposal, ProposedItem, Review

# a real quote from the MCAS card — survives the (default-on) evidence guard
REAL_QUOTE = "Quarterly fairness audits compare approval, default, and override rates"

# the four article keys of the MCAS open issues
FULL_COVERS = ["article-13", "article-14", "article-10", "article-12"]


def make_item(
    item_id,
    item_type="test",
    covers=("article-10",),
    priority="must",
    paired_test_id=None,
    evidence=None,
):
    return ProposedItem(
        item_id=item_id,
        item_type=item_type,
        priority=priority,
        rationale="r",
        evidence=[REAL_QUOTE] if evidence is None else evidence,
        covers=list(covers),
        paired_test_id=paired_test_id,
    )


class ScriptedClient:
    """Anthropic-client stub: messages.parse returns queued outputs in call
    order and records every call's kwargs."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls: list[dict] = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        return type("Parsed", (), {"parsed_output": out})()


class QueueProposer:
    """Proposer fake: returns queued proposals (last one repeats) and records
    the revision notes each round received."""

    def __init__(self, *proposals: Proposal):
        self.proposals = list(proposals) or [Proposal()]
        self.received_notes: list[str | None] = []
        self.calls = 0

    def propose(self, card, candidates, revision_notes=None, prior=None) -> Proposal:
        self.calls += 1
        self.received_notes.append(revision_notes)
        return self.proposals[min(self.calls - 1, len(self.proposals) - 1)]


class QueueReviewer:
    """Reviewer fake: returns queued reviews (last one repeats)."""

    def __init__(self, *reviews: Review):
        self.reviews = list(reviews)
        self.calls = 0

    def review(self, card, proposal) -> Review:
        self.calls += 1
        return self.reviews[min(self.calls - 1, len(self.reviews) - 1)]


class SpyAcceptReviewer:
    """Accepts everything; records the proposals it was shown."""

    def __init__(self):
        self.seen: list[Proposal] = []

    def review(self, card, proposal) -> Review:
        self.seen.append(proposal)
        return accept_all(proposal)


def accept_all(proposal: Proposal) -> Review:
    return Review(
        verdicts=[ItemVerdict(item_id=i.item_id, verdict="accept") for i in proposal.items],
        coverage_ok=True,
    )
