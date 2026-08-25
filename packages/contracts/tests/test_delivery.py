"""Which delivery guarantee each topic family is owed, and that the table is total.

``docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md`` moves a sentence in
``docs/CONTRACTS.md`` into a table. The assertions here are about the table being total
over the fifteen families and about the three sets being exactly what the record names, so
a family added without a row fails here rather than defaulting to a guarantee nobody chose.

``REQUEST_REPLY`` is asserted as its own set rather than folded into either of the others:
the queue that carries a gateway reply is a temporary one a pinned upstream component owns,
so the family is neither droppable nor backed by an endpoint this project provisions.
"""

from __future__ import annotations

import unittest
from typing import Final

from aerial_rescue_contracts.topics import Delivery, Family, delivery_for

DIRECT_FAMILY_NAMES: Final = frozenset({"DRONE_TELEMETRY", "GATEWAY_RECORD", "AGENT_RESPONSE"})

REQUEST_REPLY_FAMILY_NAMES: Final = frozenset({"GATEWAY_REQUEST", "GATEWAY_RESPONSE"})

GUARANTEED_FAMILY_NAMES: Final = frozenset(
    {
        "OPERATOR_COMMAND",
        "OPERATOR_APPROVAL",
        "DRONE_EVENT",
        "DRONE_COMMAND",
        "DRONE_COMMAND_RESULT",
        "AGENT_PROPOSAL",
        "EVIDENCE_DECISION",
        "AUDIT",
        "MISSION_EVENT",
        "SECTOR_EVENT",
    }
)


def _family_names_owed(guarantee: Delivery) -> frozenset[str]:
    """Return every family name the table binds to one guarantee."""
    return frozenset(family.name for family in Family if delivery_for(family) is guarantee)


class DeliveryTableTests(unittest.TestCase):
    def test_every_family_is_bound_to_a_guarantee(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        bound = tuple(family for family in families if delivery_for(family) in Delivery)

        # Assert
        self.assertEqual(families, bound)

    def test_the_three_guarantees_partition_the_fifteen_families(self) -> None:
        # Arrange
        expected = frozenset(family.name for family in Family)

        # Act
        covered = DIRECT_FAMILY_NAMES | REQUEST_REPLY_FAMILY_NAMES | GUARANTEED_FAMILY_NAMES

        # Assert
        self.assertEqual((expected, 15), (covered, len(covered)))

    def test_telemetry_gateway_records_and_structured_agent_responses_are_direct(self) -> None:
        # Arrange
        guarantee = Delivery.DIRECT

        # Act
        families = _family_names_owed(guarantee)

        # Assert
        self.assertEqual(DIRECT_FAMILY_NAMES, families)

    def test_the_gateway_request_and_response_own_no_project_provisioned_queue(self) -> None:
        # Arrange
        guarantee = Delivery.REQUEST_REPLY

        # Act
        families = _family_names_owed(guarantee)

        # Assert
        self.assertEqual(REQUEST_REPLY_FAMILY_NAMES, families)

    def test_every_remaining_family_is_owed_a_durable_queue(self) -> None:
        # Arrange
        guarantee = Delivery.GUARANTEED

        # Act
        families = _family_names_owed(guarantee)

        # Assert
        self.assertEqual(GUARANTEED_FAMILY_NAMES, families)

    def test_each_guarantee_carries_the_name_the_record_gives_it(self) -> None:
        # Arrange
        guarantees = (Delivery.DIRECT, Delivery.GUARANTEED, Delivery.REQUEST_REPLY)

        # Act
        names = tuple(guarantee.value for guarantee in guarantees)

        # Assert
        self.assertEqual(("direct", "guaranteed", "request-reply"), names)
