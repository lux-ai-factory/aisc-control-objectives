"""The review loop, and the rules that end it.

The risk mapper runs this loop, once per risk: a model
proposes, deterministic controls check the proposal, whatever failed goes back
with its findings, and the rounds are bounded. The policy that ends the loop is
the design, not an implementation detail, so it lives here once rather than
being written out beside each caller where the two copies can drift.

    clean     the controls objected to nothing
    fixpoint  the same findings twice running: the proposer will not fix what
              the controls will not drop, and a third attempt will not change
              that
    cap       the round limit
    failed    the model could not be reached, or answered with something that
              is not the shape asked for

**Every exit publishes.** A proposal that failed review is worth more to the
person reviewing it, with its findings attached, than nothing at all: the page
is where it gets corrected, and withholding it leaves them nothing to correct.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, TypeVar

#: Worst last, so `max(..., key=STOPS.index)` is "the worse of the two".
STOPS = ("clean", "fixpoint", "cap", "failed")
Stop = Literal["clean", "fixpoint", "cap", "failed"]

MAX_ATTEMPTS = 3

Proposal = TypeVar("Proposal")
Finding = TypeVar("Finding")


def worse(one: Stop, other: Stop) -> Stop:
    """The worse of two stops. A run's stop is the worst of its parts: one risk
    capping and a later one settling cleanly is not a clean run."""
    return max(one, other, key=STOPS.index)


class Round:
    """What one bounded review loop ended with. Always publishable."""

    def __init__(self, proposal: Proposal, findings: list, stop: Stop, error: str, attempts: int):
        self.proposal = proposal
        self.findings = findings
        self.stop: Stop = stop
        self.error = error
        self.attempts = attempts


def review(
    propose: Callable[[Sequence], Proposal],
    check: Callable[[Proposal], list],
    empty: Callable[[], Proposal],
    signature: Callable[[Sequence], frozenset],
    max_attempts: int = MAX_ATTEMPTS,
) -> Round:
    """Propose, check, re-propose what failed; bounded; always publishes.

    `propose` is given the findings of the round before. `check` returns them.
    `empty` builds what to publish when the very first attempt fails, since
    there is no proposal to fall back on. `signature` says when two rounds
    objected to the same things, which is what distinguishes a fixpoint from
    progress.
    """
    proposal = empty()
    findings: list = []
    previous: frozenset | None = None
    attempt = 0

    for attempt in range(1, max_attempts + 1):
        try:
            proposal = propose(findings)
        except Exception as exc:
            # Publish the last proposal there was, with its findings: a dead
            # provider on attempt two should not discard attempt one's work.
            return Round(proposal, findings, "failed", str(exc), attempt)

        findings = check(proposal)
        if not findings:
            return Round(proposal, findings, "clean", "", attempt)

        current = signature(findings)
        if current == previous:
            return Round(proposal, findings, "fixpoint", "", attempt)
        previous = current

    return Round(proposal, findings, "cap", "", attempt)
