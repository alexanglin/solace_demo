"""One drone's report on one command becomes a schema-bound CloudEvent, or a refusal.

The record is built and then read back through ``parse_envelope`` and
``check_topic_binding`` before it is returned, so a defect produces a refusal here rather
than an unparsable event on the broker -- the discipline ``telemetry.py`` and the command
gateway's ``record.py`` already use.

These tests supply the clock, the identifier, and the sequence rather than reading them,
which is what keeps the module pure.
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Final

from aerial_rescue_contracts.envelope import MAX_SEQUENCE, parse_envelope
from aerial_rescue_domain.commands import CommandState
from aerial_rescue_fleet_simulator.results import (
    OUTCOMES,
    ResultError,
    ResultRefusal,
    ResultStamp,
    result_record,
)

MISSION: Final = "m-2026-0001"
DRONE: Final = "drone-vision-01"
COMMAND: Final = "cmd-2026-0001"
CAUSATION: Final = "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e"
STAMP: Final = ResultStamp(
    event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6f",
    occurred_at="2026-08-23T07:31:05.117Z",
    sequence=44,
    correlation_id="c-2026-0001",
    causation_id=CAUSATION,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
)


def _refusal(state: CommandState, stamp: ResultStamp = STAMP) -> tuple[Enum, object]:
    """Return the refusal building a record raises, failing the test if it is accepted."""
    try:
        result_record(MISSION, DRONE, COMMAND, state, stamp)
    except ResultError as error:
        return (error.refusal, error.value)
    message = f"accepted: state={state!r} stamp={stamp!r}"
    raise AssertionError(message)


class RecordTests(unittest.TestCase):
    def test_the_result_is_published_on_the_command_s_own_result_topic(self) -> None:
        # Arrange
        expected = f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command-result/{COMMAND}"

        # Act
        topic, _document = result_record(MISSION, DRONE, COMMAND, CommandState.ACKNOWLEDGED, STAMP)

        # Assert
        self.assertEqual(expected, topic)

    def test_the_record_parses_as_the_bound_command_result_event(self) -> None:
        # Arrange
        expected = "aerial-rescue.v1.drone.command-result"

        # Act
        _topic, document = result_record(MISSION, DRONE, COMMAND, CommandState.SUCCEEDED, STAMP)

        # Assert
        self.assertEqual(expected, parse_envelope(document).type)

    def test_the_drone_is_the_producer_rather_than_the_process(self) -> None:
        """A result is the drone's own statement, so the source names the drone."""
        # Arrange
        expected = f"urn:aerial-rescue:drone:{DRONE}"

        # Act
        _topic, document = result_record(MISSION, DRONE, COMMAND, CommandState.FAILED, STAMP)

        # Assert
        self.assertEqual(expected, parse_envelope(document).source)

    def test_the_result_names_the_command_that_caused_it(self) -> None:
        """A retry mints a new envelope, so causation is what links a result to its send."""
        # Arrange
        expected = (CAUSATION, "c-2026-0001")

        # Act
        _topic, document = result_record(MISSION, DRONE, COMMAND, CommandState.ACKNOWLEDGED, STAMP)

        # Assert
        envelope = parse_envelope(document)
        self.assertEqual(expected, (envelope.causation_id, envelope.correlation_id))

    def test_each_reportable_state_carries_its_own_wire_word(self) -> None:
        # Arrange
        states = (CommandState.ACKNOWLEDGED, CommandState.SUCCEEDED, CommandState.FAILED)

        # Act
        words = tuple(
            parse_envelope(result_record(MISSION, DRONE, COMMAND, state, STAMP)[1]).data["outcome"]
            for state in states
        )

        # Assert
        self.assertEqual(("acknowledged", "succeeded", "failed"), words)


class VocabularyTests(unittest.TestCase):
    def test_the_table_is_total_over_the_states_a_drone_can_report(self) -> None:
        # Arrange
        expected = frozenset(
            {CommandState.ACKNOWLEDGED, CommandState.SUCCEEDED, CommandState.FAILED}
        )

        # Act
        reportable = frozenset(OUTCOMES)

        # Assert
        self.assertEqual(expected, reportable)

    def test_a_state_only_the_gateway_can_reach_is_refused(self) -> None:
        """`ABANDONED` above all: reporting it would claim the gateway's own verdict."""
        # Arrange
        expected = (ResultRefusal.UNREPORTABLE_STATE, CommandState.ABANDONED)

        # Act
        actual = _refusal(CommandState.ABANDONED)

        # Assert
        self.assertEqual(expected, actual)

    def test_a_command_not_yet_answered_cannot_be_reported_as_answered(self) -> None:
        # Arrange
        expected = (
            (ResultRefusal.UNREPORTABLE_STATE, CommandState.ACCEPTED),
            (ResultRefusal.UNREPORTABLE_STATE, CommandState.IN_FLIGHT),
        )

        # Act
        actual = (_refusal(CommandState.ACCEPTED), _refusal(CommandState.IN_FLIGHT))

        # Assert
        self.assertEqual(expected, actual)


class SequenceRefusalTests(unittest.TestCase):
    def test_a_sequence_the_envelope_form_cannot_carry_is_refused(self) -> None:
        # Arrange
        beyond = ResultStamp(
            event_id=STAMP.event_id,
            occurred_at=STAMP.occurred_at,
            sequence=MAX_SEQUENCE + 1,
            correlation_id=STAMP.correlation_id,
            causation_id=STAMP.causation_id,
            traceparent=STAMP.traceparent,
        )

        # Act
        actual = _refusal(CommandState.ACKNOWLEDGED, beyond)

        # Assert
        self.assertEqual((ResultRefusal.SEQUENCE_RANGE, MAX_SEQUENCE + 1), actual)

    def test_a_record_that_fails_its_own_profile_is_refused_before_it_is_published(self) -> None:
        # Arrange
        malformed = ResultStamp(
            event_id="NOT-AN-IDENTIFIER",
            occurred_at=STAMP.occurred_at,
            sequence=STAMP.sequence,
            correlation_id=STAMP.correlation_id,
            causation_id=STAMP.causation_id,
            traceparent=STAMP.traceparent,
        )

        # Act
        refusal, _value = _refusal(CommandState.ACKNOWLEDGED, malformed)

        # Assert
        self.assertEqual(ResultRefusal.UNPUBLISHABLE, refusal)


if __name__ == "__main__":
    unittest.main()
