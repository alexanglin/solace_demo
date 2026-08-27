"""Bounded command-outbox publication and explicit ambiguity reconciliation."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from aerial_rescue_broker.messaging import MessagingError, MessagingRefusal
from aerial_rescue_command_gateway.publication import (
    APPLICATION_PRODUCER,
    COMMAND_PUBLICATION_BATCH_SIZE,
    ApplicationPublicationError,
    ApplicationPublicationRefusal,
    CommandPublication,
    PublicationError,
    PublicationRefusal,
    publish_application_batch,
    publish_batch,
    reconcile_publication,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.outbox import StagedCommand

from .fixture_paths import repository_root

ROOT = repository_root(Path(__file__))


def _payload() -> bytes:
    """Return an exact committed rescue-escalation command event."""
    return (
        ROOT / "fixtures/golden/v1/event/drone-command-escalate-rescue/baseline.json"
    ).read_bytes()


def _command(command_id: str = "command-synthetic-0002") -> StagedCommand:
    """Return one staged command row."""
    document = canonical.decode(_payload())
    assert isinstance(document, dict)
    data = document["data"]
    assert isinstance(data, dict)
    data["commandId"] = command_id
    return StagedCommand(
        command_id=command_id,
        mission_id="mission-synthetic-0001",
        drone_id="drone-synthetic-01",
        payload=canonical.canonical_bytes(document),
        correlation_id="correlation-synthetic-0001",
        causation_id=None,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
        staged_at="2026-08-25T12:05:00.000Z",
    )


def _assign_sector_command() -> StagedCommand:
    """Return one staged assign-sector row bound to its committed fixture."""
    payload = (
        ROOT / "fixtures/golden/v1/event/drone-command-assign-sector/baseline.json"
    ).read_bytes()
    document = canonical.decode(payload)
    assert isinstance(document, dict)
    data = document["data"]
    assert isinstance(data, dict)
    return StagedCommand(
        command_id=str(data["commandId"]),
        mission_id=str(data["missionId"]),
        drone_id=str(data["droneId"]),
        payload=payload,
        correlation_id=str(document["correlationid"]),
        causation_id=None,
        traceparent=str(document["traceparent"]),
        staged_at="2026-08-25T12:05:00.000Z",
    )


class FakeCommandOutbox:
    """Return a configured batch and record only explicit publication evidence."""

    def __init__(self, pending: tuple[CommandPublication, ...]) -> None:
        """Configure the ordered rows returned by the bounded read."""
        self.rows = pending
        self.requested_limits: list[int] = []
        self.recorded: list[tuple[str, OutboxState, OutboxEvent, str | None]] = []

    async def pending(self, limit: int) -> tuple[CommandPublication, ...]:
        """Return the configured rows and retain the requested bound."""
        self.requested_limits.append(limit)
        return self.rows

    async def record(
        self,
        command_id: str,
        was: OutboxState,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one compare-and-set requested by the worker."""
        self.recorded.append((command_id, was, event, confirmed_at))


class FakePublisher:
    """Publish synchronously and inject a classified broker outcome by command identity."""

    def __init__(
        self,
        outcomes: tuple[MessagingRefusal | None, ...] = (),
    ) -> None:
        """Configure definite refusals and ambiguous failures."""
        self.outcomes = list(outcomes)
        self.sent: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Record or raise the configured classified broker failure."""
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise MessagingError(outcome, "synthetic-command")
        self.sent.append((topic, payload, properties))


class FakeConfirmationEvidence:
    """Return whether broker evidence explicitly confirms one ambiguous publication."""

    def __init__(self, confirmed: bool) -> None:
        """Configure the probe's answer."""
        self.confirmed = confirmed
        self.probed: list[str] = []

    async def confirms(self, command_id: str) -> bool:
        """Return the configured evidence and retain the exact identity."""
        self.probed.append(command_id)
        return self.confirmed


class FakeApplicationOutbox:
    """Return exact application rows and record independent publication outcomes."""

    def __init__(self, rows: tuple[StagedApplicationEvent, ...]) -> None:
        """Configure one oldest-first batch."""
        self.rows = rows
        self.producers: list[str] = []
        self.recorded: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = []

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return the configured batch and retain its producer scope."""
        self.producers.append(producer)
        return self.rows

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one explicit broker outcome."""
        self.recorded.append((identity, event, confirmed_at))


def _application_event(event_id: str = "event-audit-1") -> StagedApplicationEvent:
    """Return one exact command-gateway audit publication."""
    document = canonical.decode(
        (
            ROOT / "fixtures/golden/v1/event/audit/command-authorization/escalate-authorized.json"
        ).read_bytes()
    )
    assert isinstance(document, dict)
    document["id"] = event_id
    document["causationid"] = "operator-event-synthetic-0001"
    document["tracestate"] = "vendor=value"
    return StagedApplicationEvent(
        producer=APPLICATION_PRODUCER,
        event_id=event_id,
        family="audit",
        topic="aerial-rescue/v1/mission-synthetic-0001/audit/command-authorization",
        headers=b'{"correlation":"correlation-synthetic-0001"}',
        payload=canonical.canonical_bytes(document),
        traceparent=str(document["traceparent"]),
        tracestate=str(document["tracestate"]),
        correlation_id=str(document["correlationid"]),
        causation_id=str(document["causationid"]),
        staged_at=str(document["time"]),
    )


def _payload_with(
    event: StagedApplicationEvent,
    **members: object,
) -> StagedApplicationEvent:
    """Return a row whose exact payload carries selected replacement members."""
    document = canonical.decode(event.payload)
    assert isinstance(document, dict)
    document.update(members)
    return replace(event, payload=canonical.canonical_bytes(document))


def _payload_with_audit_type(
    event: StagedApplicationEvent,
    *,
    event_type: str,
    record_type: str,
) -> bytes:
    """Return canonical event bytes with one internally consistent audit subtype."""
    document = canonical.decode(event.payload)
    assert isinstance(document, dict)
    data = document["data"]
    assert isinstance(data, dict)
    document["type"] = event_type
    data["recordType"] = record_type
    return canonical.canonical_bytes(document)


class ApplicationPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_batch_limit_and_each_outcome_count_remain_independent(self) -> None:
        # Arrange
        rows = tuple(
            _application_event(f"event-audit-{index:02d}")
            for index in range(APPLICATION_OUTBOX_BATCH_SIZE)
        )
        outcomes = (
            MessagingRefusal.PUBLISH_REFUSED,
            None,
            MessagingRefusal.PUBLISH_AMBIGUOUS,
            MessagingRefusal.PUBLISH_REFUSED,
            None,
            MessagingRefusal.PUBLISH_AMBIGUOUS,
            *((None,) * (APPLICATION_OUTBOX_BATCH_SIZE - 6)),
        )
        store = FakeApplicationOutbox(rows)
        publisher = FakePublisher(outcomes)
        confirmed_at = "2026-08-25T12:05:01.000Z"

        # Act
        report = await publish_application_batch(store, publisher, confirmed_at)

        # Assert
        self.assertEqual(
            (
                APPLICATION_OUTBOX_BATCH_SIZE,
                APPLICATION_OUTBOX_BATCH_SIZE - 4,
                2,
                2,
                APPLICATION_OUTBOX_BATCH_SIZE - 4,
                APPLICATION_OUTBOX_BATCH_SIZE - 2,
                rows[1].topic,
                rows[1].payload,
                {"correlation": "correlation-synthetic-0001"},
            ),
            (
                report.visited,
                report.confirmed,
                report.ambiguous,
                report.refused,
                len(publisher.sent),
                len(store.recorded),
                publisher.sent[0][0],
                publisher.sent[0][1],
                publisher.sent[0][2],
            ),
        )

    async def test_every_staged_row_member_binds_to_the_exact_cloudevent_before_io(self) -> None:
        # Arrange
        event = _application_event()
        cases = (
            ("event-id", replace(event, event_id="event-audit-other")),
            ("producer", replace(event, producer="evidence-service")),
            (
                "source",
                _payload_with(
                    event,
                    source="urn:aerial-rescue:evidence-service:evidence-runtime-1",
                ),
            ),
            ("family", replace(event, family="agent-proposal")),
            (
                "event-type",
                replace(
                    event,
                    payload=_payload_with_audit_type(
                        event,
                        event_type="aerial-rescue.v1.audit.proposal-normalization",
                        record_type="proposal-normalization",
                    ),
                ),
            ),
            (
                "topic",
                replace(
                    event,
                    topic="aerial-rescue/v1/mission-synthetic-0001/audit/proposal-normalization",
                ),
            ),
            ("traceparent", replace(event, traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01")),
            ("tracestate", replace(event, tracestate=None)),
            ("correlation", replace(event, correlation_id="correlation-other")),
            ("causation", replace(event, causation_id=None)),
            ("staged-at", replace(event, staged_at="2026-08-25T12:08:02.000Z")),
        )

        # Act
        outcomes = []
        for name, mismatched in cases:
            store = FakeApplicationOutbox((mismatched,))
            publisher = FakePublisher()
            with pytest.raises(ApplicationPublicationError) as captured:
                await publish_application_batch(
                    store,
                    publisher,
                    "2026-08-25T12:09:01.000Z",
                )
            outcomes.append(
                (
                    name,
                    captured.value.refusal,
                    publisher.sent,
                    store.recorded,
                    store.producers,
                )
            )

        # Assert
        self.assertEqual(
            [
                (
                    name,
                    ApplicationPublicationRefusal.IDENTITY,
                    [],
                    [],
                    [APPLICATION_PRODUCER],
                )
                for name, _event in cases
            ],
            outcomes,
        )

    async def test_confirmation_ambiguity_and_refusal_preserve_each_row_independently(
        self,
    ) -> None:
        # Arrange
        confirmed = _application_event()
        ambiguous = _application_event("event-audit-ambiguous")
        refused = _application_event("event-audit-refused")
        store = FakeApplicationOutbox((confirmed, ambiguous, refused))
        publisher = FakePublisher(
            (
                None,
                MessagingRefusal.PUBLISH_AMBIGUOUS,
                MessagingRefusal.PUBLISH_REFUSED,
            )
        )

        # Act
        report = await publish_application_batch(
            store,
            publisher,
            "2026-08-25T12:05:01.000Z",
        )

        # Assert
        self.assertEqual(
            (
                (3, 1, 1, 1),
                [APPLICATION_PRODUCER],
                [
                    (
                        ApplicationEventIdentity(APPLICATION_PRODUCER, confirmed.event_id),
                        OutboxEvent.CONFIRM,
                        "2026-08-25T12:05:01.000Z",
                    ),
                    (
                        ApplicationEventIdentity(APPLICATION_PRODUCER, ambiguous.event_id),
                        OutboxEvent.AMBIGUOUS,
                        None,
                    ),
                ],
            ),
            (
                (report.visited, report.confirmed, report.ambiguous, report.refused),
                store.producers,
                store.recorded,
            ),
        )

    async def test_malformed_headers_are_refused_before_broker_io(self) -> None:
        # Arrange
        event = replace(_application_event(), headers=b"not-json")
        store = FakeApplicationOutbox((event,))
        publisher = FakePublisher()

        # Act
        with pytest.raises(ApplicationPublicationError) as captured:
            await publish_application_batch(
                store,
                publisher,
                "2026-08-25T12:05:01.000Z",
            )

        # Assert
        self.assertEqual(
            (
                ApplicationPublicationRefusal.HEADERS,
                ApplicationPublicationRefusal.HEADERS.value,
                [],
                [],
            ),
            (captured.value.refusal, str(captured.value), publisher.sent, store.recorded),
        )

    async def test_topic_producer_family_and_header_identity_fail_closed_before_io(self) -> None:
        # Arrange
        cases = (
            (
                replace(_application_event(), topic="not-a-topic"),
                ApplicationPublicationRefusal.IDENTITY,
            ),
            (
                replace(_application_event(), producer="other"),
                ApplicationPublicationRefusal.IDENTITY,
            ),
            (
                replace(_application_event(), family="drone-event"),
                ApplicationPublicationRefusal.IDENTITY,
            ),
            (
                replace(_application_event(), headers=canonical.canonical_bytes([])),
                ApplicationPublicationRefusal.HEADERS,
            ),
        )

        # Act
        refusals = []
        for event, _expected in cases:
            with pytest.raises(ApplicationPublicationError) as captured:
                await publish_application_batch(
                    FakeApplicationOutbox((event,)),
                    FakePublisher(),
                    "2026-08-25T12:05:01.000Z",
                )
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _event, expected in cases], refusals)

    async def test_non_text_property_keys_are_refused_at_the_decoded_boundary(self) -> None:
        # Arrange
        event = _application_event()
        store = FakeApplicationOutbox((event,))
        publisher = FakePublisher()
        decode = canonical.decode

        def decode_with_hostile_headers(value: str | bytes) -> object:
            """Keep the event valid while violating only the decoded header boundary."""
            return {1: "value"} if value == event.headers else decode(value)

        # Act
        with (
            patch(
                "aerial_rescue_command_gateway.publication.canonical.decode",
                side_effect=decode_with_hostile_headers,
            ),
            pytest.raises(ApplicationPublicationError) as captured,
        ):
            await publish_application_batch(
                store,
                publisher,
                "2026-08-25T12:05:01.000Z",
            )

        # Assert
        self.assertEqual(ApplicationPublicationRefusal.HEADERS, captured.value.refusal)

    async def test_confirmation_batch_bound_and_unknown_broker_outcome_are_refused(self) -> None:
        # Arrange
        oversized = tuple(
            _application_event(f"event-audit-{index}")
            for index in range(APPLICATION_OUTBOX_BATCH_SIZE + 1)
        )
        cases = (
            (
                FakeApplicationOutbox(()),
                FakePublisher(),
                "not-an-instant",
                ApplicationPublicationRefusal.CONFIRMATION,
            ),
            (
                FakeApplicationOutbox(oversized),
                FakePublisher(),
                "2026-08-25T12:05:01.000Z",
                ApplicationPublicationRefusal.BATCH_EXCEEDED,
            ),
            (
                FakeApplicationOutbox((_application_event(),)),
                FakePublisher((MessagingRefusal.SETTLE_REFUSED,)),
                "2026-08-25T12:05:01.000Z",
                ApplicationPublicationRefusal.BROKER_OUTCOME,
            ),
        )

        # Act
        refusals = []
        for store, publisher, confirmed_at, _expected in cases:
            with pytest.raises(ApplicationPublicationError) as captured:
                await publish_application_batch(store, publisher, confirmed_at)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for *_unused, expected in cases], refusals)


class PublicationOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_and_exact_limit_batches_preserve_zero_and_total_counts(self) -> None:
        # Arrange
        empty_store = FakeCommandOutbox(())
        full_rows = tuple(
            CommandPublication(_command(f"command-synthetic-{index:04d}"), OutboxState.STAGED)
            for index in range(COMMAND_PUBLICATION_BATCH_SIZE)
        )
        full_store = FakeCommandOutbox(full_rows)

        # Act
        empty = await publish_batch(empty_store, FakePublisher(), "2026-08-25T12:05:01.000Z")
        full = await publish_batch(full_store, FakePublisher(), "2026-08-25T12:05:02.000Z")

        # Assert
        self.assertEqual(
            (
                (0, 0, 0),
                (COMMAND_PUBLICATION_BATCH_SIZE, 0, 0),
                [COMMAND_PUBLICATION_BATCH_SIZE],
                COMMAND_PUBLICATION_BATCH_SIZE,
            ),
            (
                (empty.confirmed, empty.ambiguous, empty.refused),
                (full.confirmed, full.ambiguous, full.refused),
                full_store.requested_limits,
                len(full_store.recorded),
            ),
        )

    async def test_assign_sector_publication_preserves_exact_broker_arguments(self) -> None:
        # Arrange
        command = _assign_sector_command()
        store = FakeCommandOutbox((CommandPublication(command, OutboxState.STAGED),))
        publisher = FakePublisher()

        # Act
        report = await publish_batch(store, publisher, "2026-08-25T12:05:01.000Z")

        # Assert
        self.assertEqual(
            (
                (1, 0, 0),
                (
                    "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/command/assign-sector",
                    command.payload,
                    {},
                ),
            ),
            ((report.confirmed, report.ambiguous, report.refused), publisher.sent[0]),
        )

    async def test_confirmation_ambiguity_and_refusal_have_distinct_durable_effects(self) -> None:
        # Arrange
        confirmed = CommandPublication(_command(), OutboxState.STAGED)
        ambiguous = CommandPublication(
            _command("command-synthetic-ambiguous"),
            OutboxState.STAGED,
        )
        second_ambiguous = CommandPublication(
            _command("command-synthetic-ambiguous-2"),
            OutboxState.STAGED,
        )
        refused = CommandPublication(
            _command("command-synthetic-refused"),
            OutboxState.STAGED,
        )
        second_refused = CommandPublication(
            _command("command-synthetic-refused-2"),
            OutboxState.STAGED,
        )
        third_refused = CommandPublication(
            _command("command-synthetic-refused-3"),
            OutboxState.STAGED,
        )
        store = FakeCommandOutbox(
            (confirmed, ambiguous, second_ambiguous, refused, second_refused, third_refused)
        )
        publisher = FakePublisher(
            (
                None,
                MessagingRefusal.PUBLISH_AMBIGUOUS,
                MessagingRefusal.PUBLISH_AMBIGUOUS,
                MessagingRefusal.PUBLISH_REFUSED,
                MessagingRefusal.PUBLISH_REFUSED,
                MessagingRefusal.PUBLISH_REFUSED,
            )
        )

        # Act
        report = await publish_batch(store, publisher, "2026-08-25T12:05:01.000Z")

        # Assert
        self.assertEqual(
            (
                (1, 2, 3),
                [COMMAND_PUBLICATION_BATCH_SIZE],
                [
                    (
                        confirmed.command.command_id,
                        OutboxState.STAGED,
                        OutboxEvent.CONFIRM,
                        "2026-08-25T12:05:01.000Z",
                    ),
                    (
                        ambiguous.command.command_id,
                        OutboxState.STAGED,
                        OutboxEvent.AMBIGUOUS,
                        None,
                    ),
                    (
                        second_ambiguous.command.command_id,
                        OutboxState.STAGED,
                        OutboxEvent.AMBIGUOUS,
                        None,
                    ),
                ],
                (
                    "aerial-rescue/v1/mission-synthetic-0001/drone/drone-synthetic-01/command/escalate-rescue",
                    confirmed.command.payload,
                    {},
                ),
            ),
            (
                (report.confirmed, report.ambiguous, report.refused),
                store.requested_limits,
                store.recorded,
                publisher.sent[0],
            ),
        )

    async def test_a_store_returning_over_the_bound_is_refused_before_broker_io(self) -> None:
        # Arrange
        rows = tuple(
            CommandPublication(
                _command(f"command-synthetic-{index:04d}"),
                OutboxState.STAGED,
            )
            for index in range(COMMAND_PUBLICATION_BATCH_SIZE + 1)
        )
        store = FakeCommandOutbox(rows)
        publisher = FakePublisher()

        # Act
        with pytest.raises(PublicationError) as captured:
            await publish_batch(store, publisher, "2026-08-25T12:05:01.000Z")

        # Assert
        self.assertEqual(
            (
                PublicationRefusal.BATCH_EXCEEDED,
                PublicationRefusal.BATCH_EXCEEDED.value,
                [],
                [],
            ),
            (captured.value.refusal, str(captured.value), publisher.sent, store.recorded),
        )

    async def test_invalid_rows_states_and_broker_outcomes_fail_closed(self) -> None:
        # Arrange
        malformed = CommandPublication(replace(_command(), payload=b"not-json"), OutboxState.STAGED)
        mismatched = CommandPublication(
            replace(_command(), drone_id="drone-synthetic-02"),
            OutboxState.STAGED,
        )
        wrong_state = CommandPublication(_command(), OutboxState.RECONCILIATION_NEEDED)
        cases = (
            (FakeCommandOutbox((malformed,)), FakePublisher(), PublicationRefusal.COMMAND_BINDING),
            (FakeCommandOutbox((mismatched,)), FakePublisher(), PublicationRefusal.COMMAND_BINDING),
            (FakeCommandOutbox((wrong_state,)), FakePublisher(), PublicationRefusal.OUTBOX_STATE),
            (
                FakeCommandOutbox((CommandPublication(_command(), OutboxState.STAGED),)),
                FakePublisher((MessagingRefusal.SETTLE_REFUSED,)),
                PublicationRefusal.BROKER_OUTCOME,
            ),
        )

        # Act
        refusals = []
        for store, publisher, _expected in cases:
            with pytest.raises(PublicationError) as captured:
                await publish_batch(store, publisher, "2026-08-25T12:05:01.000Z")
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _store, _publisher, expected in cases], refusals)


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_confirmation_can_finish_an_ambiguous_publication(self) -> None:
        # Arrange
        publication = CommandPublication(_command(), OutboxState.RECONCILIATION_NEEDED)
        unconfirmed_store = FakeCommandOutbox(())
        confirmed_store = FakeCommandOutbox(())
        absent = FakeConfirmationEvidence(False)
        present = FakeConfirmationEvidence(True)

        # Act
        still_ambiguous = await reconcile_publication(
            publication,
            absent,
            unconfirmed_store,
            "2026-08-25T12:06:00.000Z",
        )
        confirmed = await reconcile_publication(
            publication,
            present,
            confirmed_store,
            "2026-08-25T12:06:01.000Z",
        )

        # Assert
        self.assertEqual(
            (
                False,
                [],
                True,
                [
                    (
                        publication.command.command_id,
                        OutboxState.RECONCILIATION_NEEDED,
                        OutboxEvent.CONFIRM,
                        "2026-08-25T12:06:01.000Z",
                    )
                ],
                [publication.command.command_id],
                [publication.command.command_id],
            ),
            (
                still_ambiguous,
                unconfirmed_store.recorded,
                confirmed,
                confirmed_store.recorded,
                absent.probed,
                present.probed,
            ),
        )

    async def test_a_non_ambiguous_row_cannot_enter_reconciliation(self) -> None:
        # Arrange
        publication = CommandPublication(_command(), OutboxState.STAGED)
        evidence = FakeConfirmationEvidence(True)
        store = FakeCommandOutbox(())

        # Act
        with pytest.raises(PublicationError) as captured:
            await reconcile_publication(
                publication,
                evidence,
                store,
                "2026-08-25T12:06:00.000Z",
            )

        # Assert
        self.assertEqual(
            (PublicationRefusal.OUTBOX_STATE, [], []),
            (captured.value.refusal, evidence.probed, store.recorded),
        )


if __name__ == "__main__":
    unittest.main()
