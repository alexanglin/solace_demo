from __future__ import annotations

import asyncio
import io
import runpy
import signal
import tempfile
import threading
import unittest
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import cast, override
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_broker.messaging import (
    ConsumingSession,
    DirectConsumingSession,
    InboundMessage,
    MessageReceiver,
    MessagingError,
    MessagingRefusal,
)
from aerial_rescue_broker.queues import RECORDER_LIFECYCLE_QUEUE
from aerial_rescue_recorder import main as recorder_main
from aerial_rescue_recorder.main import (
    LeasePort,
    RecorderConfigError,
    RecorderConfigRefusal,
    RecorderConfiguration,
    configuration,
    recorder_queue_names,
    serve,
)
from aerial_rescue_recorder.service import CaptureLoop
from aerial_rescue_store.session import DurableSession

pytestmark = [pytest.mark.unit]

ENVIRONMENT = {
    "SOLACE_BROKER_URL": "tcps://broker:55443",
    "SOLACE_BROKER_VPN": "default",
    "TRUST_STORE": "/etc/aerial-rescue/certs",
    "SOLACE_BROKER_PASSWORD_FILE": "secrets/recorder-broker-password",
    "RECORDER_READINESS_PATH": "/run/aerial-rescue/recorder/ready.json",
    "POSTGRES_USER": "aerial_rescue",
    "POSTGRES_DB": "aerial_rescue",
}
CLOSE_FAILURE = "synthetic broker close failure"


def _session_factory() -> object:
    return object()


def _secret_root(test: unittest.TestCase) -> Path:
    root = Path(test.enterContext(tempfile.TemporaryDirectory()))
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "postgres-password").write_text("not-a-real-postgres-password\n", encoding="utf-8")
    (secrets / "recorder-broker-password").write_text("r" * 64 + "\n", encoding="ascii")
    return root


class RecorderConfigurationTests(unittest.TestCase):
    def test_configuration_keeps_credentials_out_of_its_representation(self) -> None:
        # Arrange
        secret_root = _secret_root(self)

        # Act
        configured = configuration(ENVIRONMENT, secret_root=secret_root)
        rendered = repr(configured)

        # Assert
        self.assertEqual("tcps://broker:55443", configured.broker_endpoint.url)
        self.assertEqual("postgres", configured.database.host)
        self.assertNotIn("r" * 64, rendered)
        self.assertNotIn("not-a-real-postgres-password", rendered)

    def test_every_required_environment_value_refuses_when_missing_or_blank(self) -> None:
        # Arrange
        secret_root = _secret_root(self)
        names = tuple(ENVIRONMENT)

        # Act
        refusals: list[tuple[RecorderConfigRefusal, object]] = []
        for name in names:
            candidate = {**ENVIRONMENT, name: " "}
            with pytest.raises(RecorderConfigError) as captured:
                configuration(candidate, secret_root=secret_root)
            refusals.append((captured.value.refusal, captured.value.value))

        # Assert
        self.assertEqual(
            [(RecorderConfigRefusal.MISSING_SETTING, name) for name in names],
            refusals,
        )

    def test_queue_inventory_is_one_combined_lifecycle_queue_and_not_telemetry(self) -> None:
        # Arrange
        expected = (RECORDER_LIFECYCLE_QUEUE,)

        # Act
        names = recorder_queue_names()

        # Assert
        self.assertEqual(expected, names)

    def test_broker_credential_must_be_bounded_regular_ascii_secret_material(self) -> None:
        # Arrange
        secret_root = _secret_root(self)
        secret = secret_root / "secrets" / "recorder-broker-password"
        candidates = (
            b"short",
            b"\xff" * 64,
            b"x" * 4097,
        )
        refusals: list[tuple[RecorderConfigRefusal, object]] = []

        # Act
        for raw in candidates:
            secret.write_bytes(raw)
            with pytest.raises(RecorderConfigError) as captured:
                configuration(ENVIRONMENT, secret_root=secret_root)
            refusals.append((captured.value.refusal, captured.value.value))
        secret.unlink()
        secret.mkdir()
        with pytest.raises(RecorderConfigError) as captured:
            configuration(ENVIRONMENT, secret_root=secret_root)
        refusals.append((captured.value.refusal, captured.value.value))

        # Assert
        self.assertEqual(
            [
                (
                    RecorderConfigRefusal.MISSING_MATERIAL,
                    "SOLACE_BROKER_PASSWORD_FILE",
                )
            ]
            * 4,
            refusals,
        )


@dataclass
class _Runtime:
    polls: int = 0
    closes: int = 0

    async def poll_once(self) -> None:
        self.polls += 1

    async def close(self) -> None:
        self.closes += 1


class RecorderServeTests(unittest.IsolatedAsyncioTestCase):
    async def test_serve_closes_every_resource_after_the_stop_condition(self) -> None:
        # Arrange
        runtime = _Runtime()

        # Act
        await serve(runtime, stop=lambda: runtime.polls == 1)

        # Assert
        self.assertEqual((1, 1), (runtime.polls, runtime.closes))

    async def test_serve_closes_resources_when_capture_raises(self) -> None:
        # Arrange
        class _FailingRuntime(_Runtime):
            @override
            async def poll_once(self) -> None:
                raise RuntimeError

        runtime = _FailingRuntime()

        # Act
        with pytest.raises(RuntimeError):
            await serve(runtime, stop=lambda: False)

        # Assert
        self.assertEqual(1, runtime.closes)


@dataclass
class _Receiver(MessageReceiver):
    timeouts: list[int]

    @override
    def receive(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        self.timeouts.append(timeout_milliseconds)
        return None


@dataclass
class _BrokerSession:
    receiver: _Receiver
    closes: int = 0
    fail_close: bool = False

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            raise RuntimeError(CLOSE_FAILURE)


@dataclass
class _Pool:
    disposals: int = 0

    async def dispose(self) -> None:
        self.disposals += 1


@dataclass
class _Lease:
    activations: int = 0
    refreshes: int = 0
    closes: int = 0

    def activate(self) -> None:
        self.activations += 1

    def refresh_if_due(self) -> None:
        self.refreshes += 1

    def close(self) -> None:
        self.closes += 1


@dataclass
class _DurableSession(DurableSession):
    calls: list[str]

    @override
    async def commit(self) -> None:
        self.calls.append("commit")

    @override
    async def rollback(self) -> None:
        self.calls.append("rollback")

    @override
    async def close(self) -> None:
        self.calls.append("close")


class RecorderCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_runtime_constructs_receivers_only_and_closes_every_resource(self) -> None:
        # Arrange
        secret_root = _secret_root(self)
        configured = configuration(ENVIRONMENT, secret_root=secret_root)
        pool = _Pool()
        direct = _BrokerSession(_Receiver([]))
        guaranteed = (_BrokerSession(_Receiver([])),)
        lease = _Lease()

        # Act
        with (
            patch.object(recorder_main, "create_engine", return_value=pool),
            patch.object(recorder_main, "create_session_factory", return_value=_session_factory),
            patch.object(recorder_main, "_probe_store", AsyncMock()) as probed,
            patch.object(recorder_main, "ReadinessLease", return_value=lease),
            patch.object(recorder_main, "open_direct_consuming_session", return_value=direct),
            patch.object(
                recorder_main,
                "open_consuming_session",
                side_effect=guaranteed,
            ) as opened_guaranteed,
        ):
            runtime = await recorder_main.open_runtime(configured)
            await runtime.poll_once()
            await runtime.close()

        # Assert
        self.assertEqual(1, opened_guaranteed.call_count)
        self.assertEqual(1, direct.closes)
        self.assertEqual([1], [session.closes for session in guaranteed])
        self.assertEqual(1, pool.disposals)
        self.assertEqual([100], direct.receiver.timeouts)
        self.assertEqual(1, probed.await_count)
        self.assertEqual((1, 1, 2), (lease.activations, lease.refreshes, lease.closes))

    async def test_lifecycle_queue_bind_failure_closes_direct_receiver_and_store(self) -> None:
        # Arrange
        secret_root = _secret_root(self)
        configured = configuration(ENVIRONMENT, secret_root=secret_root)
        pool = _Pool()
        direct = _BrokerSession(_Receiver([]))
        lease = _Lease()
        refused = MessagingError(MessagingRefusal.BIND_REFUSED, "synthetic-queue")

        # Act
        with (
            patch.object(recorder_main, "create_engine", return_value=pool),
            patch.object(recorder_main, "create_session_factory", return_value=_session_factory),
            patch.object(recorder_main, "_probe_store", AsyncMock()),
            patch.object(recorder_main, "ReadinessLease", return_value=lease),
            patch.object(recorder_main, "open_direct_consuming_session", return_value=direct),
            patch.object(
                recorder_main,
                "open_consuming_session",
                side_effect=refused,
            ),
            pytest.raises(MessagingError),
        ):
            await recorder_main.open_runtime(configured)

        # Assert
        self.assertEqual(1, direct.closes)
        self.assertEqual(1, pool.disposals)
        self.assertEqual((0, 2), (lease.activations, lease.closes))

    async def test_runtime_close_releases_later_receivers_and_pool_after_one_close_fails(
        self,
    ) -> None:
        # Arrange
        pool = _Pool()
        direct = _BrokerSession(_Receiver([]), fail_close=True)
        guaranteed = (_BrokerSession(_Receiver([])), _BrokerSession(_Receiver([])))
        lease = _Lease()
        runtime = recorder_main.RecorderRuntime(
            cast("CaptureLoop", object()),
            cast("DirectConsumingSession", direct),
            cast("tuple[ConsumingSession, ...]", guaranteed),
            pool,
            cast("LeasePort", lease),
        )

        # Act
        with pytest.raises(RuntimeError, match=CLOSE_FAILURE):
            await runtime.close()

        # Assert
        self.assertEqual(1, direct.closes)
        self.assertEqual([1, 1], [session.closes for session in guaranteed])
        self.assertEqual(1, pool.disposals)
        self.assertEqual(1, lease.closes)

    async def test_store_probe_failure_prevents_receiver_binding_and_readiness_activation(
        self,
    ) -> None:
        # Arrange
        secret_root = _secret_root(self)
        configured = configuration(ENVIRONMENT, secret_root=secret_root)
        pool = _Pool()
        lease = _Lease()

        # Act
        with (
            patch.object(recorder_main, "create_engine", return_value=pool),
            patch.object(recorder_main, "create_session_factory", return_value=_session_factory),
            patch.object(recorder_main, "_probe_store", AsyncMock(side_effect=RuntimeError)),
            patch.object(recorder_main, "ReadinessLease", return_value=lease),
            patch.object(recorder_main, "open_direct_consuming_session") as direct,
            pytest.raises(RuntimeError),
        ):
            await recorder_main.open_runtime(configured)

        # Assert
        direct.assert_not_called()
        self.assertEqual(1, pool.disposals)
        self.assertEqual((0, 2), (lease.activations, lease.closes))

    async def test_event_transaction_commits_and_closes_the_durable_session(self) -> None:
        # Arrange
        calls: list[str] = []
        session = _DurableSession(calls)

        # Act
        async with recorder_main._event_transaction(lambda: session) as yielded:
            calls.append("body")

        # Assert
        self.assertIs(session, yielded)
        self.assertEqual(["body", "commit", "close"], calls)

    async def test_run_opens_then_closes_the_composed_runtime(self) -> None:
        # Arrange
        runtime = _Runtime()
        stopping = threading.Event()
        stopping.set()
        configured = cast("RecorderConfiguration", object())

        # Act
        with patch.object(recorder_main, "open_runtime", AsyncMock(return_value=runtime)):
            await recorder_main._run(configured, stopping)

        # Assert
        self.assertEqual((0, 1), (runtime.polls, runtime.closes))


class RecorderEntryPointTests(unittest.TestCase):
    def test_main_reports_success_without_exposing_configuration(self) -> None:
        # Arrange
        configured = cast("RecorderConfiguration", object())

        def close_coroutine(awaitable: Coroutine[object, object, object]) -> None:
            awaitable.close()

        # Act
        with (
            patch.object(recorder_main, "configuration", return_value=configured),
            patch.object(asyncio, "run", side_effect=close_coroutine),
            patch.object(signal, "signal") as signals,
        ):
            status = recorder_main.main(environment={})

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(2, signals.call_count)

    def test_main_maps_expected_startup_refusal_to_one_redacted_failure(self) -> None:
        # Arrange
        error = io.StringIO()
        refusal = RecorderConfigError(RecorderConfigRefusal.MISSING_SETTING, "setting")

        # Act
        with (
            patch.object(recorder_main, "configuration", side_effect=refusal),
            patch.object(signal, "signal"),
        ):
            status = recorder_main.main(environment={}, error=error)

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("FAILED: recorder unavailable\n", error.getvalue())
        self.assertNotIn("setting", error.getvalue())

    def test_module_entrypoint_is_guarded_and_exits_with_main_status(self) -> None:
        # Arrange
        module_file = recorder_main.__file__
        entrypoint = Path(module_file).with_name("__main__.py")

        # Act
        unguarded = runpy.run_path(str(entrypoint), run_name="recorder_import_probe")
        with (
            patch.object(recorder_main, "main", return_value=7),
            pytest.raises(SystemExit) as captured,
        ):
            runpy.run_path(str(entrypoint), run_name="__main__")

        # Assert
        self.assertIn("main", unguarded)
        self.assertEqual(7, captured.value.code)
