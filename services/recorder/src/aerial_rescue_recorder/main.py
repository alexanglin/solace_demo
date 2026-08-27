"""Production composition root for receiver-only dashboard-event capture."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
import sys
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol, TextIO, cast, override

from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    ConsumingSession,
    DirectConsumingSession,
    MessagingError,
    open_consuming_session,
    open_direct_consuming_session,
)
from aerial_rescue_broker.queues import RECORDER_LIFECYCLE_QUEUE
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store import StoreError
from aerial_rescue_store.bounds import SHUTDOWN_GRACE_SECONDS
from aerial_rescue_store.dashboard.events import EventSession
from aerial_rescue_store.dashboard.runs import RunSession, current_run
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.session import (
    Disposable,
    DurableSession,
    close,
    create_session_factory,
    transaction,
)
from aerial_rescue_store.settings import CONTAINER_HOST, DatabaseSettings, database_settings

from aerial_rescue_recorder.capture import CaptureProcessor
from aerial_rescue_recorder.database import engine_bounds
from aerial_rescue_recorder.readiness import ReadinessLease, ReadinessLeaseError
from aerial_rescue_recorder.service import CaptureLoop, DashboardAppender

RECEIVE_TIMEOUT_MILLISECONDS: Final = 100
MAXIMUM_CAPTURE_BATCH_MESSAGES: Final = 64
DEFAULT_SECRET_ROOT: Final = Path("/run")
MAXIMUM_SECRET_BYTES: Final = 4096
MINIMUM_SECRET_CHARACTERS: Final = 32
_REQUIRED_SETTINGS: Final = (
    "SOLACE_BROKER_URL",
    "SOLACE_BROKER_VPN",
    "TRUST_STORE",
    "SOLACE_BROKER_PASSWORD_FILE",
    "RECORDER_READINESS_PATH",
    "POSTGRES_USER",
    "POSTGRES_DB",
)


class RecorderConfigRefusal(Enum):
    """Why the live recorder cannot be composed."""

    MISSING_SETTING = "required recorder setting is absent or blank"
    MISSING_MATERIAL = "required recorder secret material is unavailable"


class RecorderConfigError(ValueError):
    """A redacted recorder configuration refusal."""

    def __init__(self, refusal: RecorderConfigRefusal, value: object) -> None:
        """Retain only the structured refusal and setting name."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True, repr=False)
class RecorderConfiguration:
    """Validated live endpoints with credentials deliberately omitted from repr."""

    broker_endpoint: BrokerEndpoint
    broker_credential: str
    database: DatabaseSettings
    readiness_path: Path

    @override
    def __repr__(self) -> str:
        """Render endpoints while keeping both credentials structurally redacted."""
        return (
            "RecorderConfiguration("
            f"broker_endpoint={self.broker_endpoint!r}, "
            f"database={self.database!r}, readiness_path={self.readiness_path!r}, "
            "credentials=<redacted>)"
        )


def configuration(
    environment: Mapping[str, str],
    *,
    secret_root: Path = DEFAULT_SECRET_ROOT,
) -> RecorderConfiguration:
    """Resolve every required value before constructing a broker or store client."""
    values: dict[str, str] = {}
    for name in _REQUIRED_SETTINGS:
        value = environment.get(name, "").strip()
        if not value:
            raise RecorderConfigError(RecorderConfigRefusal.MISSING_SETTING, name)
        values[name] = value
    broker = BrokerEndpoint(
        values["SOLACE_BROKER_URL"],
        values["SOLACE_BROKER_VPN"],
        values["TRUST_STORE"],
    )
    database = database_settings(values, secret_root, host=CONTAINER_HOST)
    broker_credential = _read_secret(
        _secret_path(values["SOLACE_BROKER_PASSWORD_FILE"], secret_root),
        "SOLACE_BROKER_PASSWORD_FILE",
    )
    return RecorderConfiguration(
        broker,
        broker_credential,
        database,
        Path(values["RECORDER_READINESS_PATH"]),
    )


def _secret_path(value: str, root: Path) -> Path:
    """Resolve relative test seams below the injected root; production uses absolute mounts."""
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_secret(path: Path, setting: str) -> str:
    """Read one bounded regular ASCII broker credential without following a symlink."""
    try:
        details = path.lstat()
    except OSError as invalid:
        raise RecorderConfigError(RecorderConfigRefusal.MISSING_MATERIAL, setting) from invalid
    if not stat.S_ISREG(details.st_mode) or details.st_size > MAXIMUM_SECRET_BYTES:
        raise RecorderConfigError(RecorderConfigRefusal.MISSING_MATERIAL, setting)
    try:
        raw = path.read_bytes()
        secret = raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError) as invalid:
        raise RecorderConfigError(RecorderConfigRefusal.MISSING_MATERIAL, setting) from invalid
    if len(secret) < MINIMUM_SECRET_CHARACTERS or b"\x00" in raw:
        raise RecorderConfigError(RecorderConfigRefusal.MISSING_MATERIAL, setting)
    return secret


def recorder_queue_names() -> tuple[str, ...]:
    """Return the recorder's one causally ordered guaranteed lifecycle queue."""
    return (RECORDER_LIFECYCLE_QUEUE,)


@asynccontextmanager
async def _event_transaction(
    factory: Callable[[], DurableSession],
) -> AsyncIterator[EventSession]:
    async with transaction(factory) as session:
        yield cast("EventSession", session)


class RuntimePort(Protocol):
    """One capture runtime with explicit bounded polling and shutdown."""

    async def poll_once(self) -> None:
        """Process one fair bounded poll cycle."""

    async def close(self) -> None:
        """Release broker receivers and the durable pool."""


class LeasePort(Protocol):
    """The active readiness lease owned by exactly one recorder runtime."""

    def activate(self) -> None:
        """Publish the first freshness claim."""

    def refresh_if_due(self) -> None:
        """Refresh the claim when its local schedule is due."""

    def close(self) -> None:
        """Withdraw the claim."""


@dataclass
class RecorderRuntime:
    """Owned receiver sessions and store pool behind the capture loop."""

    loop: CaptureLoop
    direct_session: DirectConsumingSession
    guaranteed_sessions: tuple[ConsumingSession, ...]
    pool: Disposable
    lease: LeasePort

    async def poll_once(self) -> None:
        """Delegate one fair cycle to the bounded capture loop."""
        await self.loop.poll_once()
        self.lease.refresh_if_due()

    async def close(self) -> None:
        """Stop every receiver before disposing the durable pool."""
        failure: BaseException | None = None
        try:
            self.lease.close()
        except BaseException as error:
            failure = error
        closers: tuple[Callable[[], None], ...] = (
            self.direct_session.close,
            *(session.close for session in self.guaranteed_sessions),
        )
        for close_session in closers:
            try:
                close_session()
            except BaseException as error:
                if failure is None:
                    failure = error
        try:
            await close(self.pool, SHUTDOWN_GRACE_SECONDS)
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure


async def open_runtime(configured: RecorderConfiguration) -> RecorderRuntime:
    """Open only receiver broker sessions and a bounded durable store pool."""
    lease = ReadinessLease(configured.readiness_path)
    lease.close()
    pool = create_engine(configured.database, engine_bounds())
    factory = create_session_factory(pool)

    def transactions() -> AbstractAsyncContextManager[EventSession]:
        durable_factory = cast("Callable[[], DurableSession]", factory)
        return _event_transaction(durable_factory)

    direct: DirectConsumingSession | None = None
    guaranteed: list[ConsumingSession] = []
    try:
        await _probe_store(cast("Callable[[], DurableSession]", factory))
        direct = open_direct_consuming_session(
            configured.broker_endpoint,
            Principal.RECORDER,
            configured.broker_credential,
            (subscription_for(Family.DRONE_TELEMETRY),),
        )
        for queue in recorder_queue_names():
            guaranteed.append(
                open_consuming_session(
                    configured.broker_endpoint,
                    Principal.RECORDER,
                    configured.broker_credential,
                    queue,
                )
            )
        lease.activate()
    except BaseException:
        lease.close()
        if direct is not None:
            direct.close()
        for session in guaranteed:
            session.close()
        await close(pool, SHUTDOWN_GRACE_SECONDS)
        raise
    appender = DashboardAppender(transactions)
    processor = CaptureProcessor(appender)
    loop = CaptureLoop(
        direct.receiver,
        tuple(session.receiver for session in guaranteed),
        processor,
        RECEIVE_TIMEOUT_MILLISECONDS,
        MAXIMUM_CAPTURE_BATCH_MESSAGES,
    )
    return RecorderRuntime(loop, direct, tuple(guaranteed), pool, lease)


async def _probe_store(factory: Callable[[], DurableSession]) -> None:
    """Prove the migrated dashboard store is reachable before claiming readiness."""
    async with transaction(factory) as session:
        await current_run(cast("RunSession", session), shared=True)


async def serve(runtime: RuntimePort, stop: Callable[[], bool]) -> None:
    """Capture until requested to stop, always closing every owned resource."""
    try:
        while not stop():
            await runtime.poll_once()
    finally:
        await runtime.close()


async def _run(configured: RecorderConfiguration, stop: threading.Event) -> None:
    runtime = await open_runtime(configured)
    await serve(runtime, stop.is_set)


def main(
    *,
    environment: Mapping[str, str] | None = None,
    error: TextIO = sys.stderr,
) -> int:
    """Run live capture, reporting a redacted nonzero outcome on expected refusal."""
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda _number, _frame: stopping.set())
    signal.signal(signal.SIGINT, lambda _number, _frame: stopping.set())
    try:
        supplied = os.environ if environment is None else environment
        asyncio.run(_run(configuration(supplied), stopping))
    except MessagingError, ReadinessLeaseError, RecorderConfigError, StoreError:
        error.write("FAILED: recorder unavailable\n")
        return 1
    return 0
