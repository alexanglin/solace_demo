"""Producer-scoped sequence admission and the known-identifier decision.

``docs/CONTRACTS.md`` scopes the fifteen-digit sequence to its producer, uses it only to
reject stale updates within one stream, and never to order the timeline; the append-only
audit ordinal does that (``docs/adr/0003-postgres-durable-mission-store.md``). Command
handlers return the prior result for a known command identifier, and approval consumption
is excluded from that replay-as-success rule: a repeat is denied
(``docs/adr/0006-proposal-bound-single-use-approvals.md``). The envelope parser has already
enforced the zero-padded wire form, so a sequence arrives here as an integer. This module is
pure: it performs no input or output and reads no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SequenceVerdict(Enum):
    """What one candidate sequence means for its producer's stream."""

    ADVANCES = "sequence advances the producer's stream"
    DUPLICATE = "sequence repeats the last accepted one; the event is a duplicate"
    STALE = "sequence is behind the producer's stream; the update is rejected"


@dataclass(frozen=True)
class Stream:
    """One producer's high-water mark; ``None`` before the first accepted event."""

    last_accepted: int | None = None


@dataclass(frozen=True)
class Reception:
    """The verdict on a candidate and the stream to carry forward."""

    verdict: SequenceVerdict
    stream: Stream


class IdempotencyKind(Enum):
    """Which operation a known identifier was seen for."""

    COMMAND = "command"
    APPROVAL_CONSUMPTION = "approval consumption"
    DASHBOARD_START = "dashboard start"
    DASHBOARD_RESET = "dashboard reset"
    DASHBOARD_COMMAND = "dashboard command"
    DASHBOARD_DECISION = "dashboard decision"


DASHBOARD_IDEMPOTENCY_KINDS = (
    IdempotencyKind.DASHBOARD_START,
    IdempotencyKind.DASHBOARD_RESET,
    IdempotencyKind.DASHBOARD_COMMAND,
    IdempotencyKind.DASHBOARD_DECISION,
)
"""The four public mutation operations whose repeats return their exact response."""


class IdempotencyDecision(Enum):
    """What a handler does with an identifier it has or has not seen."""

    EXECUTE = "first sight of the identifier; execute"
    RETURN_PRIOR_RESULT = "known command identifier; return the prior result without executing"
    DENY = "known approval consumption; deny, never an idempotent success"


def receive(stream: Stream, sequence: int) -> Reception:
    """Judge one candidate against its producer's stream.

    Args:
        stream: The producer's stream as last carried forward.
        sequence: The candidate's sequence number.

    Returns:
        The verdict, with the stream advanced only when the candidate is newer.
    """
    last = stream.last_accepted
    if last is None or sequence > last:
        return Reception(SequenceVerdict.ADVANCES, Stream(sequence))
    if sequence == last:
        return Reception(SequenceVerdict.DUPLICATE, stream)
    return Reception(SequenceVerdict.STALE, stream)


def idempotency_decision(kind: IdempotencyKind, *, known: bool) -> IdempotencyDecision:
    """Decide what a handler does with an identifier.

    Args:
        kind: The operation the identifier belongs to.
        known: Whether the identifier has been seen before.

    Returns:
        Execute on first sight; return the prior result for a known repeatable operation; deny
        a known approval consumption, which is the documented exception to replay-as-success.
    """
    if not known:
        return IdempotencyDecision.EXECUTE
    if kind is IdempotencyKind.APPROVAL_CONSUMPTION:
        return IdempotencyDecision.DENY
    return IdempotencyDecision.RETURN_PRIOR_RESULT
