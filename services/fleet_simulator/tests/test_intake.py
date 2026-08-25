"""What a simulated drone accepts off its own command queue, and what it refuses.

The refusal order is part of the contract, in the way ``parse_envelope``'s is: the topic
decides routing before the bytes are read at all, so a command for another drone is refused
without this drone ever parsing a payload another drone owns. Everything after that is read
defensively, because ``parse_envelope`` validates the envelope profile and the canonical
profile of the payload but never the payload against its own schema.

Nothing here settles a message or reads a clock. This module is pure, and these tests hand
it bytes and a topic rather than a broker message.
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_domain.authority import CommandType
from aerial_rescue_fleet_simulator.intake import (
    IntakeError,
    IntakeRefusal,
    accept,
)

MISSION: Final = "m-2026-0001"
DRONE: Final = "drone-vision-01"
COMMAND: Final = "cmd-2026-0001"
SECTOR: Final = "sector-04"
EVENT_ID: Final = "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e"
CORRELATION: Final = "c-2026-0001"
TOPIC: Final = f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command/assign-sector"
SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-command-assign-sector.schema.json"
)


def _document(**changes: object) -> dict[str, object]:
    """Return one valid assign-sector command, with any member replaced."""
    document: dict[str, object] = {
        "specversion": "1.0",
        "id": EVENT_ID,
        "source": "urn:aerial-rescue:service:command-gateway",
        "type": "aerial-rescue.v1.drone.command.assign-sector",
        "subject": MISSION,
        "time": "2026-08-23T07:31:04.882Z",
        "datacontenttype": "application/json",
        "dataschema": SCHEMA,
        "data": {
            "missionId": MISSION,
            "droneId": DRONE,
            "commandId": COMMAND,
            "sectorId": SECTOR,
        },
        "sequence": "000000000000002",
        "correlationid": CORRELATION,
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
    }
    document.update(changes)
    return document


def _payload(**changes: object) -> bytes:
    """Return the valid command's bytes, with any payload member replaced or removed."""
    document = _document()
    data = dict(document["data"])  # type: ignore[call-overload]
    for name, value in changes.items():
        if value is ...:
            del data[name]
        else:
            data[name] = value
    document["data"] = data
    return canonical.canonical_bytes(document)


def _bytes(**changes: object) -> bytes:
    """Return one command's bytes, with any envelope member replaced."""
    return canonical.canonical_bytes(_document(**changes))


def _refusal(payload: bytes | None, topic: str = TOPIC, drone: str = DRONE) -> tuple[Enum, object]:
    """Return the refusal accepting raises, failing the test if the command is accepted."""
    try:
        accept(payload, topic, drone, MISSION)
    except IntakeError as error:
        return (error.refusal, error.value)
    message = f"accepted: topic={topic!r} drone={drone!r}"
    raise AssertionError(message)


class AcceptanceTests(unittest.TestCase):
    def test_a_command_on_this_drone_s_own_topic_is_accepted(self) -> None:
        # Arrange
        payload = _bytes()

        # Act
        command = accept(payload, TOPIC, DRONE, MISSION)

        # Assert
        self.assertEqual(
            (COMMAND, CommandType.ASSIGN_SECTOR, SECTOR),
            (command.command_id, command.command_type, command.sector_id),
        )

    def test_the_accepted_command_carries_what_its_answer_must_quote(self) -> None:
        """A result names the command's own event as its causation and keeps the trail."""
        # Arrange
        payload = _bytes()

        # Act
        command = accept(payload, TOPIC, DRONE, MISSION)

        # Assert
        self.assertEqual(
            (EVENT_ID, CORRELATION, 2), (command.event_id, command.correlation_id, command.sequence)
        )


class RoutingRefusalTests(unittest.TestCase):
    def test_a_message_with_no_payload_is_refused_before_anything_else(self) -> None:
        # Arrange
        expected = (IntakeRefusal.NO_PAYLOAD, None)

        # Act
        actual = _refusal(None)

        # Assert
        self.assertEqual(expected, actual)

    def test_text_that_is_not_a_topic_is_refused_as_unrouted(self) -> None:
        # Arrange
        expected = IntakeRefusal.UNROUTED

        # Act
        refusal, _value = _refusal(_bytes(), topic="not a topic at all")

        # Assert
        self.assertEqual(expected, refusal)

    def test_another_drone_s_command_is_refused_without_reading_the_payload(self) -> None:
        """Routing is decided before the bytes, so this drone never parses another's command."""
        # Arrange
        other = f"aerial-rescue/v1/{MISSION}/drone/drone-thermal-02/command/assign-sector"

        # Act
        refusal, value = _refusal(b"not even canonical json", topic=other)

        # Assert
        self.assertEqual((IntakeRefusal.UNROUTED, other), (refusal, value))

    def test_another_mission_s_command_is_refused(self) -> None:
        # Arrange
        other = f"aerial-rescue/v1/m-2026-0002/drone/{DRONE}/command/assign-sector"

        # Act
        refusal, value = _refusal(_bytes(), topic=other)

        # Assert
        self.assertEqual((IntakeRefusal.UNROUTED, other), (refusal, value))

    def test_a_topic_from_another_family_is_refused(self) -> None:
        # Arrange
        telemetry = f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/telemetry"

        # Act
        refusal, value = _refusal(_bytes(), topic=telemetry)

        # Assert
        self.assertEqual((IntakeRefusal.UNROUTED, telemetry), (refusal, value))


class CommandTypeRefusalTests(unittest.TestCase):
    def test_a_command_type_outside_the_authority_table_is_refused(self) -> None:
        """The closed table decides, and this adapter never repairs a spelling."""
        # Arrange
        unknown = f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command/self-destruct"

        # Act
        refusal, value = _refusal(_bytes(), topic=unknown)

        # Assert
        self.assertEqual((IntakeRefusal.UNKNOWN_COMMAND_TYPE, "self-destruct"), (refusal, value))

    def test_a_schema_bound_rescue_escalation_reaches_the_payload_handler(self) -> None:
        """The closed wire schema is recognized even before this adapter implements its effect."""
        # Arrange
        escalation = f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command/escalate-rescue"
        document = _document(
            source="urn:aerial-rescue:command-gateway:gateway-synthetic-01",
            type="aerial-rescue.v1.drone.command.escalate-rescue",
            dataschema=(
                "https://aerial-rescue.invalid/schemas/v1/payload/"
                "drone-command-escalate-rescue.schema.json"
            ),
            data={
                "missionId": MISSION,
                "droneId": DRONE,
                "commandId": COMMAND,
                "approvalId": "approval-0001",
                "proposalId": "proposal-0001",
                "proposalDigest": "1" * 64,
                "proposalVersion": 1,
                "evidenceDecisionId": "decision-0001",
                "evidenceDecisionDigest": "2" * 64,
                "evidenceDecisionVersion": 1,
                "latitudeMicrodegrees": 45123456,
                "longitudeMicrodegrees": -75123456,
            },
        )

        # Act
        refusal, value = _refusal(canonical.canonical_bytes(document), topic=escalation)

        # Assert
        self.assertEqual((IntakeRefusal.MALFORMED_COMMAND, "sectorId"), (refusal, value))


class PayloadRefusalTests(unittest.TestCase):
    def test_bytes_that_are_not_canonical_json_are_refused(self) -> None:
        # Arrange
        expected = IntakeRefusal.UNREADABLE

        # Act
        refusal, _value = _refusal(b"{not json")

        # Assert
        self.assertEqual(expected, refusal)

    def test_canonical_json_that_is_not_an_envelope_is_refused(self) -> None:
        # Arrange
        expected = IntakeRefusal.UNREADABLE

        # Act
        refusal, _value = _refusal(canonical.canonical_bytes({"missionId": MISSION}))

        # Assert
        self.assertEqual(expected, refusal)

    def test_a_payload_naming_another_drone_does_not_bind_to_the_topic(self) -> None:
        # Arrange
        expected = IntakeRefusal.TOPIC_DISAGREEMENT

        # Act
        refusal, _value = _refusal(_payload(droneId="drone-thermal-02"))

        # Assert
        self.assertEqual(expected, refusal)

    def test_a_payload_with_no_command_identifier_is_refused(self) -> None:
        # Arrange
        expected = (IntakeRefusal.MALFORMED_COMMAND, "commandId")

        # Act
        actual = _refusal(_payload(commandId=...))

        # Assert
        self.assertEqual(expected, actual)

    def test_a_payload_with_no_sector_is_refused(self) -> None:
        # Arrange
        expected = (IntakeRefusal.MALFORMED_COMMAND, "sectorId")

        # Act
        actual = _refusal(_payload(sectorId=...))

        # Assert
        self.assertEqual(expected, actual)

    def test_a_command_identifier_that_is_not_a_string_is_refused(self) -> None:
        """The payload is never validated against its schema at runtime, so this is read."""
        # Arrange
        expected = (IntakeRefusal.MALFORMED_COMMAND, "commandId")

        # Act
        actual = _refusal(_payload(commandId=7))

        # Assert
        self.assertEqual(expected, actual)

    def test_a_command_identifier_outside_the_identifier_form_is_refused(self) -> None:
        # Arrange
        expected = (IntakeRefusal.MALFORMED_COMMAND, "commandId")

        # Act
        actual = _refusal(_payload(commandId="CMD-2026-0001"))

        # Assert
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
