"""A structurally isolated local replay graph."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_contracts.envelope import Envelope


class RunMode(Enum):
    """The explicit presentation and composition mode of a recorder event stream."""

    REPLAY = "replay"


class ReplayRefusal(Enum):
    """Why a validated local replay cannot be observed."""

    INVALID_BOUND = "replay event bound must be a positive integer"
    EVENT_LIMIT = "validated replay exceeds the event bound"
    ORDINAL_ORDER = "validated replay events are not one ordered gap-free stream"


class ReplayError(ValueError):
    """A replay refused before the first dashboard observation."""

    def __init__(self, refusal: ReplayRefusal, value: object) -> None:
        """Retain a structured refusal without retaining event payloads."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class OrderedReplayEvent:
    """One validated historical event carrying its authoritative audit ordinal."""

    audit_ordinal: int
    event: Envelope


class ValidatedReplaySource(Protocol):
    """A local source that validates the complete recording before returning events."""

    def load(self) -> Sequence[OrderedReplayEvent]:
        """Return the complete validated replay without opening an outbound connection."""


class ReplayObserver(Protocol):
    """The in-process production dashboard-facing event path."""

    def observe(self, mode: RunMode, event: OrderedReplayEvent, /) -> None:
        """Observe historical state with explicit replay labeling and no mutation authority."""


@dataclass(frozen=True)
class ReplayGraph:
    """The complete replay composition, containing no effectful capability."""

    source: ValidatedReplaySource
    observer: ReplayObserver
    max_events: int

    def run(self) -> int:
        """Validate bounds and order completely, then feed the local observer."""
        events = self.source.load()
        if len(events) > self.max_events:
            raise ReplayError(ReplayRefusal.EVENT_LIMIT, self.max_events)
        _validate_order(events)
        for event in events:
            self.observer.observe(RunMode.REPLAY, event)
        return len(events)


def compose_replay(
    source: ValidatedReplaySource,
    observer: ReplayObserver,
    max_events: int,
) -> ReplayGraph:
    """Construct replay without accepting a broker, publisher, writer, model, or executor."""
    if type(max_events) is not int or max_events <= 0:
        raise ReplayError(ReplayRefusal.INVALID_BOUND, "redacted-bound")
    return ReplayGraph(source, observer, max_events)


def _validate_order(events: Sequence[OrderedReplayEvent]) -> None:
    """Require a positive, gap-free authoritative audit sequence before observation."""
    for expected, event in enumerate(events, start=1):
        if event.audit_ordinal != expected:
            raise ReplayError(ReplayRefusal.ORDINAL_ORDER, expected)
