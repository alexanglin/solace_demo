"""The demo's causal chain, live: one salient event → one A2A task → one proposal → evidence.

Each case publishes one contract-built salient ``DRONE_EVENT`` as the fleet simulator and reads the
consequence from an oracle that needs no broker identity of its own: the running stack already holds
every one-connection identity at its ADR-0168 ceiling, so a second observer under ``recorder`` or
``agent-mesh-agent`` is refused before it can subscribe. The request hop is read from the
coordinator's A2A queue counter over the administrator's SEMP monitor plane; the normalised
answer and its score are read from the shared PostgreSQL store, which is also where the demo says
the chain must land durably. The mission identifier comes from the operator's running mission
(``AERIAL_RESCUE_DEMO_MISSION_ID``); an absent identifier fails the case, it never skips it.

What a green run establishes, and no more: the Event Mesh Gateway delivered one request to the
coordinator's A2A queue; the command gateway normalised the coordinator's answer into one
``candidate-location`` proposal bound to the published source event and digest; and the evidence
service scored that proposal ``contributing`` at 75 (``corroborated``). An abstaining model leaves
no proposal, which the second case reports by name; nothing here proves the dashboard rendered
anything.

Markers keep these out of every blocking suite: they need Docker, the broker, the ``services``
profile (command gateway and evidence service), PostgreSQL, and Ollama.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
import unittest
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final

import pytest
from aerial_rescue_broker.deployment import DEFAULT_VPN
from aerial_rescue_broker.messaging import open_guaranteed_publishing_session
from aerial_rescue_broker.semp import SempSession, connect
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
from aerial_rescue_recorder.console import production_bounds
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.settings import database_settings
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.broker_live_support import (
    DEPLOY_ROOT,
    LOCAL_BROKER_ENDPOINT,
    administrator_semp_endpoint,
    role_credential,
)

pytestmark = [pytest.mark.phase0, pytest.mark.docker, pytest.mark.broker, pytest.mark.ollama]

MISSION_SETTING: Final = "AERIAL_RESCUE_DEMO_MISSION_ID"
DRONE: Final = "drone-sim-07"
EVENT_TYPE: Final = "salient"
TARGET_AGENT: Final = "MissionCoordinator"
PROPOSAL_TYPE: Final = "candidate-location"
A2A_QUEUE_MARKER: Final = "/a2a/"

# The same two windows the gateway probe already found sufficient: the request needs no model;
# the proposal and the decision wait for a cold local model plus two service hops.
REQUEST_WINDOW_SECONDS: Final = 60
# The coordinator answers a structured request in one model turn, but the local model on a
# loaded workstation has been observed taking two minutes per turn; this window covers several
# turns plus the two service hops that follow, and a failure at its end is a real absence.
RESPONSE_WINDOW_SECONDS: Final = 900
POLL_INTERVAL_SECONDS: Final = 1.0
SOURCE_DIGEST_PROPERTY: Final = "aerial-rescue-source-event-digest"
CORROBORATED_SCORE: Final = 75
EXPECTED_BAND: Final = "corroborated"
EXPECTED_OUTCOME: Final = "contributing"

_SOURCE_EVENT_QUERY: Final = text(
    "select source, canonical_digest from source_event "
    "where mission_id = :mission and event_id = :event"
)
_PROPOSAL_QUERY: Final = text(
    "select proposal_id, agent_name, proposal_type, source_event_digest, drone_id "
    "from proposal where mission_id = :mission and source_event_id = :event"
)
_DECISION_QUERY: Final = text(
    "select outcome, score, band from evidence_decision "
    "where mission_id = :mission and proposal_id = :proposal"
)


class _UnpublishableEventError(RuntimeError):
    """The event this test intends to publish is not one the contract accepts."""


def _mission_id() -> str:
    """Return the operator's mission identifier or raise so the case fails rather than skips."""
    value = os.environ.get(MISSION_SETTING, "").strip()
    if not value:
        message = f"{MISSION_SETTING} must name the running mission; the chain has nowhere to land"
        raise _UnpublishableEventError(message)
    return value


def _traceparent() -> str:
    """Mint a W3C traceparent whose identifiers are non-zero, as the envelope contract requires."""
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def _salient_event(mission: str) -> tuple[str, bytes, str, Envelope]:
    """Return one accepted salient event, its canonical bytes, its digest, and the envelope."""
    topic = Topic(Family.DRONE_EVENT, mission, {"droneId": DRONE, "eventType": EVENT_TYPE})
    declared = event_type(topic)
    envelope = Envelope(
        id=str(uuid.uuid7()),
        source=f"urn:aerial-rescue:drone:{DRONE}",
        type=declared,
        subject=mission,
        time=datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        dataschema=binding_for(declared).dataschema,
        sequence=f"{int(time.time() * 1000) % 10**15:015d}",
        correlation_id=f"c-{secrets.token_hex(4)}",
        traceparent=_traceparent(),
        data={
            "missionId": mission,
            "droneId": DRONE,
            "observation": "artifact-sighting",
            "latitudeMicrodegrees": 44_477_100,
            "longitudeMicrodegrees": -79_242_900,
            "detail": "A bright synthetic marker on open rock, held across two passes.",
        },
    )
    document = envelope_document(envelope)
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as refusal:
        message = f"the contract refused the event this test publishes: {refusal}"
        raise _UnpublishableEventError(message) from refusal
    return (
        format_topic(topic),
        canonical.canonical_bytes(document),
        source_event_digest(envelope),
        envelope,
    )


def _publish(topic: str, payload: bytes, digest: str) -> None:
    """Publish one guaranteed salient event the way the fleet does.

    The project's own publisher is used rather than a bare SDK one because it injects the native
    Solace trace context beside the envelope's ``traceparent``. Every durable application consumer
    validates that the two agree (``packages/broker`` ``_validate_trace``), so a message published
    without it is refused ``native-trace-refused`` and dead-lettered by the recorder and the
    evidence service, leaving the proposal with no provenance to score.
    """
    session = open_guaranteed_publishing_session(
        LOCAL_BROKER_ENDPOINT,
        Principal.FLEET_SIMULATOR,
        role_credential(Principal.FLEET_SIMULATOR),
    )
    try:
        session.publisher.publish(topic, payload, {SOURCE_DIGEST_PROPERTY: digest})
    finally:
        session.close()


def _a2a_deliveries() -> int:
    """Return how many messages the mesh's A2A queues have spooled in total.

    Every agent binds its own temporary ``.../q/a2a/<agent>`` queue, and ``spooledMsgCount``
    is cumulative, so a rise across the set is one delivered task. The sum rather than the
    coordinator's own queue is the oracle because which queue carries a delegated request is
    the Connector's business, not this test's.
    """
    endpoint = administrator_semp_endpoint()
    connection = connect(endpoint)
    try:
        rows = SempSession(connection, endpoint).read_monitor(
            f"msgVpns/{DEFAULT_VPN}/queues?select=queueName,spooledMsgCount"
        )
    finally:
        connection.close()
    total = 0
    matched = False
    for row in rows:
        name = row.get("queueName")
        count = row.get("spooledMsgCount")
        if isinstance(name, str) and A2A_QUEUE_MARKER in name and isinstance(count, int):
            matched = True
            total += count
    if not matched:
        message = f"no queue name contains {A2A_QUEUE_MARKER}; is the Agent Mesh running?"
        raise AssertionError(message)
    return total


def _store_engine() -> AsyncEngine:
    """Open a bounded read-only view of the shared store the services persist into."""
    return create_engine(database_settings(os.environ, DEPLOY_ROOT), production_bounds())


async def _one_row(
    engine: AsyncEngine, query: TextClause, parameters: Mapping[str, str]
) -> Mapping[str, object] | None:
    async with engine.connect() as connection:
        result = await connection.execute(query, parameters)
        row = result.mappings().first()
        return None if row is None else dict(row)


async def _await_row(
    seconds: int,
    read: Callable[[], Awaitable[Mapping[str, object] | None]],
) -> Mapping[str, object] | None:
    """Poll one read until it answers or the monotonic deadline passes."""
    deadline = time.monotonic() + seconds
    while True:
        row = await read()
        if row is not None or time.monotonic() >= deadline:
            return row
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _stored_source_event(
    mission: str, event_id: str, seconds: int
) -> Mapping[str, object] | None:
    """Wait for the evidence service to persist the published event as its provenance."""
    engine = _store_engine()
    try:
        return await _await_row(
            seconds,
            lambda: _one_row(engine, _SOURCE_EVENT_QUERY, {"mission": mission, "event": event_id}),
        )
    finally:
        await engine.dispose()


async def _proposal_then_decision(
    mission: str, event_id: str, seconds: int
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None]:
    """Wait for the normalised proposal and then for its evidence decision within one window."""
    engine = _store_engine()
    try:
        started = time.monotonic()
        proposal = await _await_row(
            seconds,
            lambda: _one_row(engine, _PROPOSAL_QUERY, {"mission": mission, "event": event_id}),
        )
        if proposal is None:
            return None, None
        remaining = max(1, int(seconds - (time.monotonic() - started)))
        decision = await _await_row(
            remaining,
            lambda: _one_row(
                engine,
                _DECISION_QUERY,
                {"mission": mission, "proposal": str(proposal["proposal_id"])},
            ),
        )
        return proposal, decision
    finally:
        await engine.dispose()


def _wait_for_requests(before: int, seconds: int) -> int:
    """Poll the A2A queue counters until they rise or the monotonic deadline passes."""
    deadline = time.monotonic() + seconds
    count = _a2a_deliveries()
    while count <= before and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        count = _a2a_deliveries()
    return count


class SalientChainTests(unittest.TestCase):
    def test_the_published_event_is_persisted_as_the_evidence_service_s_provenance(self) -> None:
        # Arrange
        mission = _mission_id()
        topic, payload, digest, envelope = _salient_event(mission)

        # Act
        _publish(topic, payload, digest)
        stored = asyncio.run(_stored_source_event(mission, envelope.id, REQUEST_WINDOW_SECONDS))

        # Assert
        self.assertIsNotNone(
            stored,
            "the evidence service stored no source event: the publication was refused before it "
            "could become provenance, so nothing downstream can be scored",
        )
        assert stored is not None
        self.assertEqual(f"urn:aerial-rescue:drone:{DRONE}", stored["source"])

    def test_the_salient_event_becomes_one_a2a_task_for_the_mission_coordinator(self) -> None:
        # Arrange
        topic, payload, digest, _envelope = _salient_event(_mission_id())
        before = _a2a_deliveries()

        # Act
        _publish(topic, payload, digest)
        after = _wait_for_requests(before, REQUEST_WINDOW_SECONDS)

        # Assert
        self.assertGreater(
            after,
            before,
            "the mesh's A2A queues spooled nothing, so the salient event became no task",
        )

    def test_the_coordinator_s_answer_becomes_one_proposal_bound_to_the_source_event(self) -> None:
        # Arrange
        mission = _mission_id()
        topic, payload, digest, envelope = _salient_event(mission)

        # Act
        _publish(topic, payload, digest)
        proposal, _decision = asyncio.run(
            _proposal_then_decision(mission, envelope.id, RESPONSE_WINDOW_SECONDS)
        )

        # Assert
        self.assertIsNotNone(
            proposal,
            "no proposal was normalised within the window: the coordinator abstained, the owned "
            "gateway published no candidate, or the command gateway refused it",
        )
        assert proposal is not None
        self.assertEqual(
            (TARGET_AGENT, PROPOSAL_TYPE, digest, DRONE),
            (
                proposal["agent_name"],
                proposal["proposal_type"],
                proposal["source_event_digest"],
                proposal["drone_id"],
            ),
        )

    def test_a_candidate_proposal_earns_a_corroborated_evidence_decision(self) -> None:
        # Arrange
        mission = _mission_id()
        topic, payload, digest, envelope = _salient_event(mission)

        # Act
        _publish(topic, payload, digest)
        proposal, decision = asyncio.run(
            _proposal_then_decision(mission, envelope.id, RESPONSE_WINDOW_SECONDS)
        )

        # Assert
        self.assertIsNotNone(proposal, "no proposal was normalised, so nothing could be scored")
        self.assertIsNotNone(decision, "the evidence service published no decision in the window")
        assert decision is not None
        self.assertEqual(
            (EXPECTED_OUTCOME, CORROBORATED_SCORE, EXPECTED_BAND),
            (decision["outcome"], decision["score"], decision["band"]),
        )


if __name__ == "__main__":
    unittest.main()
