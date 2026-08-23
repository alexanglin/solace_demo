"""What a simulated drone accepts off its own command queue, and what it refuses.

The refusal order is part of the contract, the way ``parse_envelope``'s is. The topic is
read first and decides routing, so a command addressed to another drone or another mission
is refused before this drone parses a payload it does not own; then the command type
against the closed authority table; then the bytes; then the envelope against the topic;
then the payload's own members.

Those last members are read defensively rather than trusted. ``parse_envelope`` validates
the envelope profile and the canonical profile of the payload, and it checks that the
subject is the payload's mission, but it never validates the payload against its own JSON
Schema -- that oracle runs offline over the golden fixtures. A payload member that reaches
a fold is therefore one this module checked.

Nothing here decides settlement, drives a machine, or reads a clock. The composition root
takes the bytes off a message and decides what to do with a refusal. This module is pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    EnvelopeError,
    EnvelopeRefusal,
    check_topic_binding,
    decode_envelope,
)
from aerial_rescue_contracts.topics import (
    DRONE_PARAMETER,
    IDENTIFIER_PATTERN,
    Family,
    Topic,
    TopicError,
    parse_topic,
)
from aerial_rescue_domain import DomainError
from aerial_rescue_domain.authority import CommandType, command_type

from aerial_rescue_fleet_simulator import FleetSimulatorError

COMMAND_TYPE_PARAMETER: Final = "commandType"
COMMAND_ID_MEMBER: Final = "commandId"
SECTOR_ID_MEMBER: Final = "sectorId"

_IDENTIFIER: Final = re.compile(IDENTIFIER_PATTERN)


class IntakeRefusal(Enum):
    """Why a message on this drone's command queue is not a command it may act on."""

    NO_PAYLOAD = "message carries no payload"
    UNROUTED = "topic is not this drone's command family in this run's mission"
    UNKNOWN_COMMAND_TYPE = "command type is outside the closed authority table"
    UNREADABLE = "payload is not an application event envelope"
    UNBOUND_COMMAND_TYPE = "command type has no payload schema bound to it"
    TOPIC_DISAGREEMENT = "envelope does not bind to the topic it arrived on"
    MALFORMED_COMMAND = "payload does not carry the members its command type needs"


class IntakeError(FleetSimulatorError):
    """A message intake refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class IncomingCommand:
    """One command this drone accepted, reduced to what a fold and an answer need.

    ``sector_id`` is here because ``assign-sector`` is the one command type a payload
    schema binds today. Binding a second type changes this shape, which is the coupling
    ``docs/adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md`` accepts
    in exchange for the type-to-payload agreement being checked.
    """

    command_id: str
    command_type: CommandType
    sector_id: str
    event_id: str
    correlation_id: str
    sequence: int


def _routed(topic: str, drone_id: str, mission_id: str) -> Topic:
    """Return the topic this message arrived on, refusing one this drone does not own."""
    try:
        parsed = parse_topic(topic)
    except TopicError as refusal:
        raise IntakeError(IntakeRefusal.UNROUTED, topic) from refusal
    addressed = (
        parsed.family is Family.DRONE_COMMAND
        and parsed.mission_id == mission_id
        and parsed.parameters.get(DRONE_PARAMETER) == drone_id
    )
    if not addressed:
        raise IntakeError(IntakeRefusal.UNROUTED, topic)
    return parsed


def _identifier(payload: Mapping[str, object], member: str) -> str:
    """Return one identifier-formed payload member, refusing an absent or ill-formed one.

    ``parse_envelope`` has already made the payload a mapping inside the canonical profile,
    so the shape is not re-checked here; what it has not done is validate the payload
    against its own schema, which is why every member a fold reads passes through here.
    """
    value = payload.get(member)
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise IntakeError(IntakeRefusal.MALFORMED_COMMAND, member)
    return value


def accept(payload: bytes | None, topic: str, drone_id: str, mission_id: str) -> IncomingCommand:
    """Return the command these bytes carry, or refuse them with a structured reason.

    Args:
        payload: The message body, or ``None`` when the message carried none.
        topic: The topic the message arrived on, as the broker reported it.
        drone_id: The drone whose queue this is.
        mission_id: The mission this run is flying.

    Returns:
        The accepted command.

    Raises:
        IntakeError: In the order this module documents, carrying the refusal and the
            value that caused it.
    """
    if payload is None:
        raise IntakeError(IntakeRefusal.NO_PAYLOAD, None)
    parsed = _routed(topic, drone_id, mission_id)
    kind = parsed.parameters[COMMAND_TYPE_PARAMETER]
    try:
        requested = command_type(kind)
    except DomainError as refusal:
        raise IntakeError(IntakeRefusal.UNKNOWN_COMMAND_TYPE, kind) from refusal
    try:
        envelope = decode_envelope(payload)
    except canonical.CanonicalizationError as refusal:
        raise IntakeError(IntakeRefusal.UNREADABLE, str(refusal)) from refusal
    except EnvelopeError as refusal:
        raise _unreadable(refusal) from refusal
    try:
        check_topic_binding(envelope, parsed)
    except EnvelopeError as refusal:
        raise IntakeError(IntakeRefusal.TOPIC_DISAGREEMENT, refusal.value) from refusal
    return IncomingCommand(
        command_id=_identifier(envelope.data, COMMAND_ID_MEMBER),
        command_type=requested,
        sector_id=_identifier(envelope.data, SECTOR_ID_MEMBER),
        event_id=envelope.id,
        correlation_id=envelope.correlation_id,
        sequence=int(envelope.sequence),
    )


def _unreadable(refusal: EnvelopeError) -> IntakeError:
    """Return the refusal an unparsable envelope becomes, naming an unbound type as such.

    A command type the authority table knows but no payload schema binds is a different
    fact from malformed bytes: it is the state ADR-0082 leaves ``escalate-rescue`` in, so a
    drone that meets one has met a command the system cannot yet express rather than a
    corrupt message. Both are deterministic, so both are refused; only the diagnosis
    differs, and it differs on the audit trail.
    """
    if refusal.refusal is EnvelopeRefusal.UNKNOWN_TYPE:
        return IntakeError(IntakeRefusal.UNBOUND_COMMAND_TYPE, refusal.value)
    return IntakeError(IntakeRefusal.UNREADABLE, str(refusal))
