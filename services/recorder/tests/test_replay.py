"""Structurally isolated replay over a validated local source and dashboard observer."""

from __future__ import annotations

import inspect
import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from typing import Final

import pytest
from aerial_rescue_contracts.envelope import Envelope
from aerial_rescue_recorder.replay import (
    OrderedReplayEvent,
    ReplayError,
    ReplayRefusal,
    RunMode,
    compose_replay,
)

MISSION: Final = "mission-1"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"


def _event(event_id: str, instant: str) -> Envelope:
    """Return one historical command event visible only through the local adapter."""
    return Envelope(
        id=event_id,
        source="urn:aerial-rescue:dashboard-api:run-1",
        type="aerial-rescue.v1.operator.command.escalate-rescue",
        subject=MISSION,
        time=instant,
        dataschema=(
            "https://aerial-rescue.invalid/schemas/v1/payload/operator-command.schema.json"
        ),
        sequence="000000000000001",
        correlation_id="correlation-1",
        traceparent=TRACEPARENT,
        data={"missionId": MISSION, "action": {"commandType": "escalate-rescue"}},
    )


@dataclass
class _Source:
    """Return a fully validated, bounded replay sequence."""

    events: Sequence[OrderedReplayEvent]
    loads: int = 0

    def load(self) -> Sequence[OrderedReplayEvent]:
        """Load the local replay events without opening a connection."""
        self.loads += 1
        return self.events


@dataclass
class _Observer:
    """Record the production dashboard-facing local observations."""

    seen: list[tuple[RunMode, int, str]] = field(default_factory=list)

    def observe(self, mode: RunMode, event: OrderedReplayEvent, /) -> None:
        """Observe one historical event without creating any effectful sink."""
        self.seen.append((mode, event.audit_ordinal, event.event.id))


class ReplayCompositionTests(unittest.TestCase):
    def test_nonpositive_or_boolean_replay_bounds_are_refused_before_source_load(self) -> None:
        # Arrange
        source = _Source(())
        values = (0, -1, True)

        # Act
        refusals = []
        for value in values:
            with self.subTest(value=value):
                with pytest.raises(ReplayError) as captured:
                    compose_replay(source, _Observer(), max_events=value)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            ([ReplayRefusal.INVALID_BOUND] * len(values), 0),
            (refusals, source.loads),
        )

    def test_the_replay_graph_has_no_broker_publisher_store_writer_or_model_capability(
        self,
    ) -> None:
        # Arrange
        parameters = tuple(inspect.signature(compose_replay).parameters)

        # Act
        graph = compose_replay(_Source(()), _Observer(), max_events=2)

        # Assert
        self.assertEqual(
            (
                ("source", "observer", "max_events"),
                ("source", "observer", "max_events"),
            ),
            (parameters, tuple(item.name for item in fields(graph))),
        )

    def test_an_approved_historical_command_is_observed_as_replay_and_never_actuated(self) -> None:
        # Arrange
        source = _Source((OrderedReplayEvent(1, _event("event-1", "2026-08-25T12:00:01.000Z")),))
        observer = _Observer()
        graph = compose_replay(source, observer, max_events=2)

        # Act
        count = graph.run()

        # Assert
        self.assertEqual(
            (1, 1, [(RunMode.REPLAY, 1, "event-1")]),
            (count, source.loads, observer.seen),
        )

    def test_unordered_or_overfull_input_is_refused_before_the_first_observation(self) -> None:
        # Arrange
        event = _event("event-1", "2026-08-25T12:00:01.000Z")
        cases = (
            ((_Source((OrderedReplayEvent(2, event),))), 2, ReplayRefusal.ORDINAL_ORDER),
            (
                _Source((OrderedReplayEvent(1, event), OrderedReplayEvent(2, event))),
                1,
                ReplayRefusal.EVENT_LIMIT,
            ),
        )

        # Act
        refusals = []
        observations = []
        for source, bound, expected in cases:
            with self.subTest(expected=expected):
                observer = _Observer()
                with pytest.raises(ReplayError) as captured:
                    compose_replay(source, observer, max_events=bound).run()
                refusals.append(captured.value.refusal)
                observations.append(observer.seen)

        # Assert
        self.assertEqual(
            ([ReplayRefusal.ORDINAL_ORDER, ReplayRefusal.EVENT_LIMIT], [[], []]),
            (refusals, observations),
        )


if __name__ == "__main__":
    unittest.main()
