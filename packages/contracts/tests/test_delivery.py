"""Which delivery guarantee each topic family is owed, and that the table is total.

``docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md`` moves a sentence in
``docs/CONTRACTS.md`` into a table. The assertions here are about the table being total
over the eleven families and about the three sets being exactly what the record names, so
a family added without a row fails here rather than defaulting to a guarantee nobody chose.

``REQUEST_REPLY`` is asserted as its own set rather than folded into either of the others:
the queue that carries a gateway reply is a temporary one a pinned upstream component owns,
so the family is neither droppable nor backed by an endpoint this project provisions.
"""

from __future__ import annotations

import unittest
from typing import Final

from aerial_rescue_contracts.topics import Delivery, Family, delivery_for

DIRECT_FAMILIES: Final = frozenset({Family.DRONE_TELEMETRY})

REQUEST_REPLY_FAMILIES: Final = frozenset({Family.GATEWAY_REQUEST, Family.GATEWAY_RESPONSE})

GUARANTEED_FAMILIES: Final = frozenset(
    {
        Family.OPERATOR_COMMAND,
        Family.OPERATOR_APPROVAL,
        Family.DRONE_EVENT,
        Family.DRONE_COMMAND,
        Family.DRONE_COMMAND_RESULT,
        Family.AGENT_PROPOSAL,
        Family.AGENT_RESPONSE,
        Family.AUDIT,
    }
)


def _families_owed(guarantee: Delivery) -> frozenset[Family]:
    """Return every family the table binds to one guarantee."""
    return frozenset(family for family in Family if delivery_for(family) is guarantee)


class DeliveryTableTests(unittest.TestCase):
    def test_every_family_is_bound_to_a_guarantee(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        bound = tuple(family for family in families if delivery_for(family) in Delivery)

        # Assert
        self.assertEqual(families, bound)

    def test_the_three_guarantees_partition_the_eleven_families(self) -> None:
        # Arrange
        expected = frozenset(Family)

        # Act
        covered = DIRECT_FAMILIES | REQUEST_REPLY_FAMILIES | GUARANTEED_FAMILIES

        # Assert
        self.assertEqual((expected, 11), (covered, len(covered)))

    def test_routine_telemetry_is_the_only_direct_family(self) -> None:
        # Arrange
        guarantee = Delivery.DIRECT

        # Act
        families = _families_owed(guarantee)

        # Assert
        self.assertEqual(DIRECT_FAMILIES, families)

    def test_the_gateway_request_and_response_own_no_project_provisioned_queue(self) -> None:
        # Arrange
        guarantee = Delivery.REQUEST_REPLY

        # Act
        families = _families_owed(guarantee)

        # Assert
        self.assertEqual(REQUEST_REPLY_FAMILIES, families)

    def test_every_remaining_family_is_owed_a_durable_queue(self) -> None:
        # Arrange
        guarantee = Delivery.GUARANTEED

        # Act
        families = _families_owed(guarantee)

        # Assert
        self.assertEqual(GUARANTEED_FAMILIES, families)

    def test_each_guarantee_carries_the_name_the_record_gives_it(self) -> None:
        # Arrange
        guarantees = (Delivery.DIRECT, Delivery.GUARANTEED, Delivery.REQUEST_REPLY)

        # Act
        names = tuple(guarantee.value for guarantee in guarantees)

        # Assert
        self.assertEqual(("direct", "guaranteed", "request-reply"), names)
