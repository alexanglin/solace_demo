"""Whether the pinned Event Mesh Gateway turns one salient CloudEvent into one A2A task.

This is the ingress half of the Phase 0 Event Mesh spike. Everything before it was evidence
about configuration: the offline validator proves a file's shape, and
``quality_gate_tests/deploy/agent_mesh/test_agent_mesh_gateway_config.py`` proves the
committed file stays inside the grants ``docs/adr/0061`` gives its role. Neither is
evidence that a message moves.

The event these publish is validated by the contract rather than hand-written: it is built as an
``Envelope``, emitted through ``envelope_document``, checked against the topic it will be sent
to, and serialised with the canonical encoder. If any of that refused, the test would fail
before the broker was involved, which is what makes "one *validated* salient CloudEvent" a claim
rather than a description.

Three assertions, deliberately of different kinds. The first is model-independent: the gateway
creating an A2A request is a transformation, not a judgement, so it either happens or the spike
has failed. The second depends on a local model and asserts only that an answer was routed back
onto the agent-response family, never what the answer said. The third is the failure path: an
event the handler cannot decode must produce no task at all.

What is **not** asserted here, because no durable queue exists and
``docs/adr/0071`` records why: redelivery, dead-message handling, and any behaviour of an event
published while the gateway is disconnected.

Markers keep these out of every blocking suite: they need Docker, the broker, and Ollama.
"""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.solace_properties import message_properties
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription

from tests.broker_live_support import SHARED_PROBE_DRONES, connected_native_role_service

pytestmark = [pytest.mark.phase0, pytest.mark.docker, pytest.mark.broker, pytest.mark.ollama]

NAMESPACE = "aerial-rescue-mesh"

MISSION = "m-2026-0001"
DRONE = SHARED_PROBE_DRONES[2]
EVENT_TYPE = "salient"
TARGET_AGENT = "MissionCoordinator"

A2A_REQUEST_TOPIC = f"{NAMESPACE}/a2a/v1/agent/request/{TARGET_AGENT}"
AGENT_RESPONSE_TOPIC = format_topic(
    Topic(Family.AGENT_RESPONSE, MISSION, {"agentName": TARGET_AGENT})
)

# The gateway builds its A2A request without consulting a model, so a request that has not
# appeared in this window is not slow, it is absent. The response window is the other kind:
# it covers a cold local model, and is the delegation window test_agent_mesh_live.py already
# found sufficient plus the gateway hop.
REQUEST_WINDOW_SECONDS = 60
RESPONSE_WINDOW_SECONDS = 240
SILENCE_WINDOW_SECONDS = 45
RECEIVE_POLL_MILLISECONDS = 1000
ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS = 10000
SOURCE_DIGEST_PROPERTY = "aerial-rescue-source-event-digest"


class _UnpublishableEventError(RuntimeError):
    """The event this test intends to publish is not one the contract accepts."""


def _salient_event() -> tuple[str, bytes, str]:
    """Return one accepted salient event and its complete source-envelope digest.

    Raises:
        _UnpublishableEventError: If the contract refuses the event, so a broken fixture can
            never be mistaken for a broken gateway.
    """
    topic = Topic(Family.DRONE_EVENT, MISSION, {"droneId": DRONE, "eventType": EVENT_TYPE})
    declared = event_type(topic)
    envelope = Envelope(
        id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6c",
        source=f"urn:aerial-rescue:drone:{DRONE}",
        type=declared,
        subject=MISSION,
        time="2026-08-21T18:42:11.004Z",
        dataschema=binding_for(declared).dataschema,
        sequence="000000000000043",
        correlation_id="c-2026-0001",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01",
        data={
            "missionId": MISSION,
            "droneId": DRONE,
            "observation": "thermal-contact",
            "latitudeMicrodegrees": 47123901,
            "longitudeMicrodegrees": -122653114,
            "detail": "A persistent warm signature under canopy, held across three passes.",
        },
    )
    document = envelope_document(envelope)
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as refusal:
        message = f"the contract refused the event this test publishes: {refusal}"
        raise _UnpublishableEventError(message) from refusal
    return format_topic(topic), canonical.canonical_bytes(document), source_event_digest(envelope)


def _publish(publication: tuple[str, bytes, str]) -> None:
    """Publish one guaranteed message as the fleet simulator and wait for the broker's answer."""
    topic, payload, digest = publication
    service = connected_native_role_service(Principal.FLEET_SIMULATOR)
    try:
        publisher = service.create_persistent_message_publisher_builder().build()
        publisher.start()
        # The builder takes a bytearray or a str, never bytes; the canonical encoder emits
        # bytes, so the conversion is here rather than at every call site.
        message = service.message_builder().build(
            bytearray(payload),
            additional_message_properties={
                SOURCE_DIGEST_PROPERTY: digest,
                message_properties.PERSISTENT_ACK_IMMEDIATELY: True,
                message_properties.PERSISTENT_DMQ_ELIGIBLE: True,
            },
        )
        publisher.publish_await_acknowledgement(
            message, SolaceTopic.of(topic), ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS
        )
        publisher.terminate()
    finally:
        service.disconnect()


def _observe_while_publishing(
    role: Principal,
    subscription: str,
    seconds: int,
    publication: tuple[str, bytes, str],
) -> list[str]:
    """Return what ``subscription`` carries after one event is published beneath it.

    The receiver starts before the publish, because a direct message nobody is subscribed to
    yet is gone rather than delayed. Receiving is blocking rather than by callback so that
    nothing here subclasses the untyped upstream handler
    (``docs/adr/0028-untyped-solace-client-boundary.md``).
    """
    service = connected_native_role_service(role)
    receiver = service.create_direct_message_receiver_builder().with_subscriptions(
        [TopicSubscription.of(subscription)]
    )
    seen: list[str] = []
    built = receiver.build()
    try:
        built.start()
        _publish(publication)
        for _ in range(seconds):
            message = built.receive_message(timeout=RECEIVE_POLL_MILLISECONDS)
            if message is not None:
                seen.append(message.get_destination_name())
                break
    finally:
        built.terminate()
        service.disconnect()
    return seen


class SalientEventIngressTests(unittest.TestCase):
    def test_one_salient_cloud_event_becomes_one_a2a_task(self) -> None:
        # Arrange
        publication = _salient_event()

        # Act
        observed = _observe_while_publishing(
            Principal.AGENT_MESH_AGENT,
            A2A_REQUEST_TOPIC,
            REQUEST_WINDOW_SECONDS,
            publication,
        )

        # Assert
        self.assertEqual(
            [A2A_REQUEST_TOPIC],
            observed,
            "the gateway produced no A2A request, so the CloudEvent was not transformed",
        )

    def test_the_agent_answer_is_routed_back_onto_the_agent_response_family(self) -> None:
        # Arrange
        publication = _salient_event()

        # Act
        observed = _observe_while_publishing(
            Principal.RECORDER,
            AGENT_RESPONSE_TOPIC,
            RESPONSE_WINDOW_SECONDS,
            publication,
        )

        # Assert
        self.assertEqual(
            [AGENT_RESPONSE_TOPIC],
            observed,
            "the gateway's output handler routed nothing back to the agent-response family",
        )


class UndecodableEventTests(unittest.TestCase):
    def test_an_event_the_handler_cannot_decode_produces_no_task(self) -> None:
        # Arrange
        topic, _, digest = _salient_event()
        payload = b"this is not the JSON the handler declares"

        # Act
        observed = _observe_while_publishing(
            Principal.AGENT_MESH_AGENT,
            A2A_REQUEST_TOPIC,
            SILENCE_WINDOW_SECONDS,
            (topic, payload, digest),
        )

        # Assert
        self.assertEqual(
            [],
            observed,
            "an undecodable payload reached an agent, so the handler is not refusing it",
        )


if __name__ == "__main__":
    unittest.main()
