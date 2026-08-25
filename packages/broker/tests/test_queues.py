"""The durable queue set the grant tables imply, and the two rules that keep it honest.

``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md`` derives the queues
from tables that already exist rather than listing them: the subscribe grants in
``packages/domain`` intersected with the guaranteed families in ``packages/contracts``. The
assertions here are about that derivation being a narrowing, about the endpoint table being
total, and about ``NONE`` being unable to hide a consumer that holds a grant.

Nothing here reaches a broker. Rendering a name and choosing a subscription are pure, and
whether the broker accepts either is a live claim these tests do not make.
"""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_broker.queues import (
    DEAD_MESSAGE_QUEUE,
    MAX_QUEUE_NAME_LENGTH,
    MAX_SPOOL_MEGABYTES,
    Endpoint,
    QueueError,
    QueueRefusal,
    desired_queues,
    drone_queue_name,
    endpoint_for,
    family_queue_name,
    guaranteed_grants,
    queues_for,
)
from aerial_rescue_broker.subscriptions import drone_command_subscription, subscription_for
from aerial_rescue_contracts.topics import (
    MAX_IDENTIFIER_LENGTH,
    Delivery,
    Family,
    TopicError,
    TopicRefusal,
    delivery_for,
)
from aerial_rescue_domain.principals import (
    Access,
    Principal,
    PrincipalError,
    PrincipalRefusal,
    grants,
)

DRONE = "drone-vision-01"
OTHER_DRONE = "drone-thermal-02"
DRONES = (DRONE, OTHER_DRONE)
REFERENCE_DRONES = tuple(f"drone-{ordinal:02d}" for ordinal in range(1, 24))


class EndpointTableTests(unittest.TestCase):
    def test_every_role_is_bound_to_an_endpoint_form(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        bound = tuple(role for role in roles if endpoint_for(role) in Endpoint)

        # Assert
        self.assertEqual(roles, bound)

    def test_a_role_with_no_endpoint_holds_no_guaranteed_subscribe_grant(self) -> None:
        # Arrange
        roles = tuple(role for role in Principal if endpoint_for(role) is Endpoint.NONE)

        # Act
        owed = {role: guaranteed_grants(role) for role in roles}

        # Assert
        self.assertEqual({role: frozenset() for role in roles}, owed)

    def test_only_the_event_mesh_gateway_defers_to_an_upstream_endpoint(self) -> None:
        # Arrange
        expected = frozenset({Principal.EVENT_MESH_GATEWAY})

        # Act
        deferring = frozenset(role for role in Principal if endpoint_for(role) is Endpoint.UPSTREAM)

        # Assert
        self.assertEqual(expected, deferring)


class DerivationTests(unittest.TestCase):
    def test_a_queue_is_only_ever_owed_for_a_family_the_role_may_subscribe_to(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        widened = tuple(
            role for role in roles if not guaranteed_grants(role) <= grants(role, Access.SUBSCRIBE)
        )

        # Assert
        self.assertEqual((), widened)

    def test_every_family_owed_a_queue_is_one_the_table_calls_guaranteed(self) -> None:
        # Arrange
        owed = {family for role in Principal for family in guaranteed_grants(role)}

        # Act
        guarantees = {delivery_for(family) for family in owed}

        # Assert
        self.assertEqual({Delivery.GUARANTEED}, guarantees)

    def test_the_command_gateway_is_owed_the_three_guaranteed_inputs_the_record_names(self) -> None:
        # Arrange
        expected = frozenset(
            {
                Family.OPERATOR_COMMAND,
                Family.OPERATOR_APPROVAL,
                Family.DRONE_COMMAND_RESULT,
            }
        )

        # Act
        owed = guaranteed_grants(Principal.COMMAND_GATEWAY)

        # Assert
        self.assertEqual(expected, owed)

    def test_the_dashboard_api_has_no_queue_for_its_three_direct_inputs(self) -> None:
        # Arrange
        subscribed = grants(Principal.DASHBOARD_API, Access.SUBSCRIBE)

        # Act
        owed = guaranteed_grants(Principal.DASHBOARD_API)

        # Assert
        self.assertEqual(
            frozenset({Family.DRONE_TELEMETRY, Family.GATEWAY_RECORD, Family.AGENT_RESPONSE}),
            subscribed - owed,
        )

    def test_the_recorder_is_owed_the_ten_guaranteed_families(self) -> None:
        # Arrange
        expected = frozenset(
            family for family in Family if delivery_for(family) is Delivery.GUARANTEED
        )

        # Act
        owed = guaranteed_grants(Principal.RECORDER)

        # Assert
        self.assertEqual((expected, 10), (owed, len(owed)))


class FamilyQueueTests(unittest.TestCase):
    def test_a_family_queue_is_named_for_its_role_and_its_family(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        name = family_queue_name(role, Family.OPERATOR_APPROVAL)

        # Assert
        self.assertEqual("aerial-rescue/v1/command-gateway/operator.approval", name)

    def test_a_family_queue_is_owned_by_its_role_and_attracts_only_that_family(self) -> None:
        # Arrange
        role = Principal.EVIDENCE_SERVICE

        # Act
        queues = queues_for(role, ())

        # Assert
        self.assertEqual(
            {
                (role.value, frozenset({subscription_for(family)}))
                for family in guaranteed_grants(role)
            },
            {(queue.owner, queue.subscriptions) for queue in queues},
        )

    def test_a_family_the_table_does_not_call_guaranteed_has_no_queue(self) -> None:
        # Arrange
        family = Family.DRONE_TELEMETRY

        # Act
        with pytest.raises(QueueError) as raised:
            family_queue_name(Principal.DASHBOARD_API, family)

        # Assert
        self.assertEqual(QueueRefusal.UNGUARANTEED_FAMILY, raised.value.refusal)

    def test_a_role_that_defers_to_upstream_has_no_family_queue(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_GATEWAY

        # Act
        with pytest.raises(QueueError) as raised:
            family_queue_name(role, Family.DRONE_EVENT)

        # Assert
        self.assertEqual(QueueRefusal.NOT_A_FAMILY_CONSUMER, raised.value.refusal)

    def test_a_family_the_role_may_not_subscribe_to_is_denied_by_the_domain(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        with pytest.raises(PrincipalError) as raised:
            family_queue_name(role, Family.AUDIT)

        # Assert
        self.assertEqual(PrincipalRefusal.DENIED, raised.value.refusal)

    def test_every_rendered_family_name_is_inside_the_bound(self) -> None:
        # Arrange
        roles = tuple(role for role in Principal if endpoint_for(role) is Endpoint.FAMILY)

        # Act
        lengths = {
            len(family_queue_name(role, family))
            for role in roles
            for family in guaranteed_grants(role)
        }

        # Assert
        self.assertLess(max(lengths), MAX_QUEUE_NAME_LENGTH)


class DroneQueueTests(unittest.TestCase):
    def test_the_fleet_simulator_gets_one_queue_per_drone_and_no_family_queue(self) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        queues = queues_for(role, DRONES)

        # Assert
        self.assertEqual(
            tuple(drone_queue_name(drone) for drone in DRONES),
            tuple(queue.name for queue in queues),
        )

    def test_a_drone_queue_reaches_that_drone_and_no_other(self) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        queues = queues_for(role, DRONES)

        # Assert
        self.assertEqual(
            (frozenset({drone_command_subscription(DRONE)}), role.value),
            (queues[0].subscriptions, queues[0].owner),
        )

    def test_a_fleet_with_no_drones_declared_gets_no_command_queue(self) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        queues = queues_for(role, ())

        # Assert
        self.assertEqual((), queues)

    def test_a_drone_identifier_outside_the_identifier_form_is_refused(self) -> None:
        # Arrange
        identifier = "Drone-01"

        # Act
        with pytest.raises(TopicError) as raised:
            drone_queue_name(identifier)

        # Assert
        self.assertEqual(TopicRefusal.IDENTIFIER_FORM, raised.value.refusal)

    def test_the_longest_drone_queue_name_is_inside_the_bound(self) -> None:
        # Arrange
        longest = "d" * MAX_IDENTIFIER_LENGTH

        # Act
        name = drone_queue_name(longest)

        # Assert
        self.assertLess(len(name), MAX_QUEUE_NAME_LENGTH)

    def test_a_role_that_is_not_the_fleet_simulator_has_no_drone_queue(self) -> None:
        # Arrange
        role = Principal.RECORDER

        # Act
        queues = queues_for(role, DRONES)

        # Assert
        self.assertNotIn(drone_queue_name(DRONE), tuple(queue.name for queue in queues))


class DesiredSetTests(unittest.TestCase):
    def test_the_dead_message_queue_comes_first_and_exactly_once(self) -> None:
        # Arrange
        expected = 1

        # Act
        names = tuple(queue.name for queue in desired_queues(DRONES))

        # Assert
        self.assertEqual(
            (DEAD_MESSAGE_QUEUE, expected), (names[0], names.count(DEAD_MESSAGE_QUEUE))
        )

    def test_the_dead_message_queue_is_owned_by_nobody_and_attracts_nothing(self) -> None:
        # Arrange
        queues = desired_queues(())

        # Act
        dead = next(queue for queue in queues if queue.name == DEAD_MESSAGE_QUEUE)

        # Assert
        self.assertEqual(("", frozenset()), (dead.owner, dead.subscriptions))

    def test_the_desired_set_names_every_queue_once(self) -> None:
        # Arrange
        queues = desired_queues(DRONES)

        # Act
        names = tuple(queue.name for queue in queues)

        # Assert
        self.assertEqual(len(names), len(set(names)))

    def test_the_small_fixture_has_twenty_one_family_queues_a_drone_each_and_the_dead_letter(
        self,
    ) -> None:
        # Arrange
        expected = 21 + len(DRONES) + 1

        # Act
        queues = desired_queues(DRONES)

        # Assert
        self.assertEqual(expected, len(queues))

    def test_the_reference_fleet_reserves_forty_five_queues_and_450_megabytes(self) -> None:
        # Arrange
        expected = (45, 450)

        # Act
        queues = desired_queues(REFERENCE_DRONES)
        inventory = (len(queues), len(queues) * MAX_SPOOL_MEGABYTES)

        # Assert
        self.assertEqual(expected, inventory)
