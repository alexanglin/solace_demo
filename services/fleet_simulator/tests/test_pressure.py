"""One-shot production pressure publication through the fleet broker identity."""

from __future__ import annotations

import importlib
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Final, cast
from unittest.mock import patch

import pytest
from aerial_rescue_broker.messaging import (
    MessagePublisher,
    MessagingError,
    MessagingRefusal,
)
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_fleet_simulator import pressure
from aerial_rescue_fleet_simulator.main import broker_configuration
from aerial_rescue_fleet_simulator.pressure import (
    PressureError,
    PressureRefusal,
    PressureRequest,
    main,
    publish_pressure,
)
from aerial_rescue_fleet_simulator.service import CountingStamps

pytestmark = [pytest.mark.unit]

MISSION: Final = "mission-pressure-0001"
RUN: Final = "run-pressure-0001"
DRONE: Final = "drone-sim-07"
PRESSURE_ID: Final = "123e4567-e89b-42d3-a456-426614174000"
BROKER_VALUE: Final = "not-a-real-pressure-broker-password"


class _Publisher:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []
        self._fail_after = fail_after

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
    ) -> None:
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)
        self.calls.append((topic, payload, properties))


class _Session:
    def __init__(self, publisher: _Publisher) -> None:
        self.publisher = cast("MessagePublisher", publisher)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _environment(root: Path) -> dict[str, str]:
    broker = root / "broker"
    broker.write_text(BROKER_VALUE, encoding="ascii")
    return {
        "SOLACE_BROKER_URL": "tcps://broker:55443",
        "SOLACE_BROKER_VPN": "default",
        "TRUST_STORE": "/etc/aerial-rescue/certs",
        "SOLACE_BROKER_PASSWORD_FILE": str(broker),
    }


def _arguments(event_count: int = 4) -> tuple[str, ...]:
    return (
        "--mission-id",
        MISSION,
        "--run-id",
        RUN,
        "--drone-id",
        DRONE,
        "--pressure-id",
        PRESSURE_ID,
        "--event-count",
        str(event_count),
    )


def _stamps() -> CountingStamps:
    identifiers = (f"{2**96 + value:032x}" for value in range(1, 100))
    return CountingStamps(
        clock=lambda: datetime(2026, 8, 25, 12, tzinfo=UTC),
        identifiers=lambda: next(identifiers),
        correlation_id=RUN,
    )


class PressurePublicationTests(unittest.TestCase):
    def test_publication_uses_a_unique_source_monotonic_identity_and_real_transitions(
        self,
    ) -> None:
        # Arrange
        publisher = _Publisher()
        request = PressureRequest(MISSION, RUN, DRONE, PRESSURE_ID, 4)

        # Act
        published = publish_pressure(request, cast("MessagePublisher", publisher), _stamps())
        envelopes = tuple(decode_envelope(payload) for _, payload, _ in publisher.calls)

        # Assert
        self.assertEqual(4, published)
        self.assertEqual(
            {"urn:aerial-rescue:connectivity-lifecycle:pressure-123e4567e89b42d3a456426614174000"},
            {envelope.source for envelope in envelopes},
        )
        self.assertEqual(
            ["000000000000000", "000000000000001", "000000000000002", "000000000000003"],
            [envelope.sequence for envelope in envelopes],
        )
        self.assertEqual(
            ["DEGRADED", "CONNECTED", "DEGRADED", "CONNECTED"],
            [envelope.data["connectivity"] for envelope in envelopes],
        )
        self.assertEqual(4, len({envelope.id for envelope in envelopes}))
        self.assertTrue(
            all(
                topic == f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/event/connectivity-changed"
                for topic, _, _ in publisher.calls
            )
        )

    def test_request_refuses_invalid_identifiers_uuid_and_event_bounds(self) -> None:
        # Arrange
        cases: Sequence[tuple[tuple[object, ...], PressureRefusal]] = (
            (("NOT_VALID", RUN, DRONE, PRESSURE_ID, 257), PressureRefusal.IDENTIFIER),
            (
                (MISSION, RUN, DRONE, "123e4567-e89b-12d3-a456-426614174000", 257),
                PressureRefusal.PRESSURE_ID,
            ),
            ((MISSION, RUN, DRONE, PRESSURE_ID, 0), PressureRefusal.EVENT_COUNT),
            ((MISSION, RUN, DRONE, PRESSURE_ID, 513), PressureRefusal.EVENT_COUNT),
        )

        # Act
        refusals = []
        for values, _ in cases:
            with pytest.raises(PressureError) as captured:
                PressureRequest(*values)  # type: ignore[arg-type]
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in cases], refusals)


class PressureProcessTests(unittest.TestCase):
    def test_broker_profile_requires_no_control_listener_secret(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        configured = broker_configuration(environment)
        rendered = repr(configured)

        # Assert
        self.assertEqual("tcps://broker:55443", configured.endpoint.url)
        self.assertEqual(BROKER_VALUE, configured.credential)
        self.assertNotIn(BROKER_VALUE, rendered)

    def test_main_publishes_the_requested_count_and_always_closes_its_session(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        publisher = _Publisher()
        session = _Session(publisher)
        output = StringIO()
        errors = StringIO()

        # Act
        status = main(
            _arguments(),
            environment=environment,
            output=output,
            error=errors,
            open_session=lambda _endpoint, _role, _credential: session,
        )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(4, len(publisher.calls))
        self.assertTrue(session.closed)
        self.assertEqual("published 4 pressure events\n", output.getvalue())
        self.assertEqual("", errors.getvalue())

    def test_main_closes_after_publication_failure_and_reports_no_identifiers_or_secrets(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        session = _Session(_Publisher(fail_after=1))
        errors = StringIO()

        # Act
        status = main(
            _arguments(),
            environment=environment,
            output=StringIO(),
            error=errors,
            open_session=lambda _endpoint, _role, _credential: session,
        )

        # Assert
        self.assertEqual(1, status)
        self.assertTrue(session.closed)
        self.assertEqual("FAILED: pressure publication unavailable\n", errors.getvalue())
        self.assertNotIn(MISSION, errors.getvalue())
        self.assertNotIn(RUN, errors.getvalue())
        self.assertNotIn(BROKER_VALUE, errors.getvalue())

    def test_module_import_has_no_process_side_effect(self) -> None:
        # Arrange
        module_name = pressure.__name__

        # Act
        with patch(
            "aerial_rescue_broker.messaging.open_guaranteed_publishing_session",
            side_effect=AssertionError("import opened a broker session"),
        ) as opener:
            reloaded = importlib.reload(pressure)

        # Assert
        self.assertEqual(module_name, reloaded.__name__)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
