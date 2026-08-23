"""One drone's report on one command becomes a schema-bound CloudEvent, or a refusal.

The five members a fold could not have produced -- the identifier, the instant, the
producer sequence, the causation, and the trace parent -- arrive as a :class:`ResultStamp`
from the composition root, so this module reads no clock and consumes no random source.
The record is read back through the envelope profile and the topic binding before it is
returned, so a defect fails here rather than on the broker. That is the discipline
``telemetry.py`` and the command gateway's ``record.py`` already use.

:data:`OUTCOMES` is the wire vocabulary, written out rather than derived from the enum
member names so that renaming a state is a visible change to the wire rather than a silent
one -- the reason the command gateway's policy table gives for the same choice. It covers
the three states a drone can cause and refuses the other three: ``ACCEPTED`` and
``IN_FLIGHT`` are the dispatcher's view of its own command, and ``ABANDONED`` is the
gateway's verdict on one it stopped sending, so a drone reporting any of them would be
claiming a fact it does not hold
(``docs/adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md``).

Nothing here decides delivery. ``docs/CONTRACTS.md`` puts a command result on guaranteed
delivery; who publishes it, and with which guarantee, belongs to the composition root.

This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.commands import CommandState

from aerial_rescue_fleet_simulator import FleetSimulatorError, event_source

DRONE_PARAMETER: Final = "droneId"
COMMAND_PARAMETER: Final = "commandId"

OUTCOMES: Final[Mapping[CommandState, str]] = {
    CommandState.ACKNOWLEDGED: "acknowledged",
    CommandState.SUCCEEDED: "succeeded",
    CommandState.FAILED: "failed",
}
"""The states a drone can report, and the word each is spelled with on the wire."""


class ResultRefusal(Enum):
    """Why a report cannot become a record."""

    UNREPORTABLE_STATE = "state is one only the dispatching gateway can reach"
    SEQUENCE_RANGE = "producer sequence outside the representable range"
    UNPUBLISHABLE = "record does not satisfy the envelope profile it was built for"


class ResultError(FleetSimulatorError):
    """A record the profile refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class ResultStamp:
    """What the composition root supplies that this module may not read for itself."""

    event_id: str
    occurred_at: str
    sequence: int
    correlation_id: str
    causation_id: str
    traceparent: str


def result_record(
    mission_id: str,
    drone_id: str,
    command_id: str,
    state: CommandState,
    stamp: ResultStamp,
) -> tuple[str, dict[str, object]]:
    """Return the topic and envelope document one command result is published as.

    Args:
        mission_id: The mission every event of this run names.
        drone_id: The drone reporting, which is also the event's producer.
        command_id: The command being answered, which keys the topic and is echoed in the
            payload so the two cannot disagree.
        state: The state this drone put the command into.
        stamp: The identifier, instant, sequence, correlation, causation, and trace parent
            to send it under.

    Returns:
        The topic text and the envelope document, in that order.

    Raises:
        ResultError: With ``UNREPORTABLE_STATE`` for a state only the gateway can reach,
            ``SEQUENCE_RANGE`` for a sequence the envelope form cannot carry, or
            ``UNPUBLISHABLE`` when the built record does not satisfy the profile it claims.
    """
    word = OUTCOMES.get(state)
    if word is None:
        raise ResultError(ResultRefusal.UNREPORTABLE_STATE, state)
    rendered = sequence_text(stamp.sequence)
    if rendered is None:
        raise ResultError(ResultRefusal.SEQUENCE_RANGE, stamp.sequence)
    topic = Topic(
        Family.DRONE_COMMAND_RESULT,
        mission_id,
        {DRONE_PARAMETER: drone_id, COMMAND_PARAMETER: command_id},
    )
    declared = event_type(topic)
    document = envelope_document(
        Envelope(
            id=stamp.event_id,
            source=event_source(drone_id),
            type=declared,
            subject=mission_id,
            time=stamp.occurred_at,
            dataschema=binding_for(declared).dataschema,
            sequence=rendered,
            correlation_id=stamp.correlation_id,
            causation_id=stamp.causation_id,
            traceparent=stamp.traceparent,
            data={
                "missionId": mission_id,
                "droneId": drone_id,
                "commandId": command_id,
                "outcome": word,
            },
        )
    )
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as refusal:
        raise ResultError(ResultRefusal.UNPUBLISHABLE, str(refusal)) from refusal
    return format_topic(topic), document
