"""The session factory, and the transaction boundary a caller cannot leave open.

`engine.py` builds a pool and stops there; nothing in this member has ever opened a unit of
work over it. Everything interesting about one is a decision rather than an input or output,
so it is asserted here without a database: which two session defaults this member overrides,
that constructing a factory connects to nothing, and -- against deterministic fakes -- that
the boundary commits exactly once on a clean exit, rolls back on every other, and closes on
every path.

[ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)
is explicit about the ceiling on the fake-driven cases: they prove call order and rollback
*intent*, and nothing whatsoever about PostgreSQL transaction visibility, isolation, or what a
real connection does when the task holding it is cancelled. Those live in
`tests/integration/test_durable_store_live.py` and only there.

The connection proof is structural, as `test_engine.py`'s is: the target is a port nothing
listens on, so an eager connect would fail this test rather than quietly succeed against the
developer's own running cluster.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field, replace
from typing import Final, cast

import pytest
from aerial_rescue_store.bounds import (
    CHECKOUT_TIMEOUT_SECONDS,
    CONNECT_RETRIES,
    CONNECT_TIMEOUT_SECONDS,
    IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    LOCK_TIMEOUT_MILLISECONDS,
    POOL_OVERFLOW,
    POOL_SIZE,
    SHUTDOWN_GRACE_SECONDS,
    STATEMENT_TIMEOUT_MILLISECONDS,
    EngineBounds,
)
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.session import (
    AUTOFLUSH,
    EXPIRE_ON_COMMIT,
    SessionError,
    SessionRefusal,
    close,
    create_session_factory,
    transaction,
)
from aerial_rescue_store.settings import DatabaseSettings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

CREDENTIAL: Final = "fixture-not-a-real-credential"
UNREACHABLE_PORT: Final = 1
SETTINGS: Final = DatabaseSettings(
    host="127.0.0.1",
    port=UNREACHABLE_PORT,
    user="aerial_rescue",
    database="aerial_rescue",
    password=CREDENTIAL,
)
BOUNDS: Final = EngineBounds(
    pool_size=POOL_SIZE,
    pool_overflow=POOL_OVERFLOW,
    checkout_timeout_seconds=CHECKOUT_TIMEOUT_SECONDS,
    connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
    connect_retries=CONNECT_RETRIES,
    statement_timeout_milliseconds=STATEMENT_TIMEOUT_MILLISECONDS,
    lock_timeout_milliseconds=LOCK_TIMEOUT_MILLISECONDS,
    idle_in_transaction_timeout_milliseconds=IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
)

COMMIT: Final = "commit"
ROLLBACK: Final = "rollback"
CLOSE: Final = "close"

NO_GRACE: Final = 0
"""A grace the fake pool cannot meet, so the refusal is observed without waiting for one."""


@dataclass
class _RecordingSession:
    """A session that records the boundary's calls and does nothing else."""

    calls: list[str] = field(default_factory=list)

    async def commit(self) -> None:
        """Record that the boundary committed."""
        self.calls.append(COMMIT)

    async def rollback(self) -> None:
        """Record that the boundary rolled back."""
        self.calls.append(ROLLBACK)

    async def close(self) -> None:
        """Record that the boundary released the session."""
        self.calls.append(CLOSE)


@dataclass
class _StalledPool:
    """A pool whose disposal never completes, so only the grace can end the wait."""

    async def dispose(self) -> None:
        """Wait for an event nothing sets."""
        await asyncio.Event().wait()


@dataclass
class _ReleasingPool:
    """A pool that releases immediately and records that it was asked to."""

    disposed: bool = False

    async def dispose(self) -> None:
        """Record the disposal."""
        self.disposed = True


class SessionFactoryTests(unittest.TestCase):
    def test_a_committed_value_stays_readable_without_a_second_round_trip(self) -> None:
        # Arrange
        engine = create_engine(SETTINGS, BOUNDS)

        # Act
        factory = create_session_factory(engine)

        # Assert
        self.assertEqual((False, False), (EXPIRE_ON_COMMIT, factory.kw["expire_on_commit"]))

    def test_the_factory_never_flushes_a_write_nobody_asked_for(self) -> None:
        # Arrange
        engine = create_engine(SETTINGS, BOUNDS)

        # Act
        factory = create_session_factory(engine)

        # Assert
        self.assertEqual((False, False), (AUTOFLUSH, factory.kw["autoflush"]))

    def test_the_factory_binds_the_engine_it_was_given(self) -> None:
        # Arrange
        engine = create_engine(SETTINGS, BOUNDS)

        # Act
        factory = create_session_factory(engine)

        # Assert
        self.assertEqual((engine, AsyncSession), (factory.kw["bind"], factory.class_))

    def test_building_a_factory_against_a_dead_port_opens_no_connection(self) -> None:
        # Arrange
        engine = create_engine(replace(SETTINGS, port=UNREACHABLE_PORT), BOUNDS)

        # Act
        create_session_factory(engine)

        # Assert
        self.assertEqual(0, cast("QueuePool", engine.pool).checkedout())


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    """Fakes prove call order and rollback intent; ADR-0086 says they prove nothing more."""

    async def test_a_clean_exit_commits_once_and_then_releases_the_session(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        async with transaction(lambda: session):
            pass

        # Assert
        self.assertEqual([COMMIT, CLOSE], session.calls)

    async def test_a_failing_body_rolls_back_without_committing_and_still_releases(self) -> None:
        # Arrange
        session = _RecordingSession()
        failure = RuntimeError("the body refused")

        # Act
        with pytest.raises(RuntimeError) as raised:
            async with transaction(lambda: session):
                raise failure

        # Assert
        self.assertEqual(([ROLLBACK, CLOSE], failure), (session.calls, raised.value))

    async def test_a_cancelled_body_rolls_back_and_lets_the_cancellation_through(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        with pytest.raises(asyncio.CancelledError):
            async with transaction(lambda: session):
                raise asyncio.CancelledError

        # Assert
        self.assertEqual([ROLLBACK, CLOSE], session.calls)

    async def test_the_boundary_yields_the_session_its_factory_made(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        async with transaction(lambda: session) as opened:
            yielded = opened

        # Assert
        self.assertIs(session, yielded)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_pool_that_releases_within_the_grace_is_disposed(self) -> None:
        # Arrange
        pool = _ReleasingPool()

        # Act
        await close(pool, SHUTDOWN_GRACE_SECONDS)

        # Assert
        self.assertTrue(pool.disposed)

    async def test_a_pool_that_outlives_the_grace_is_refused_rather_than_waited_on(self) -> None:
        # Arrange
        pool = _StalledPool()

        # Act
        with pytest.raises(SessionError) as refused:
            await close(pool, NO_GRACE)

        # Assert
        self.assertEqual(
            (SessionRefusal.SHUTDOWN_TIMED_OUT, NO_GRACE),
            (refused.value.refusal, refused.value.value),
        )


if __name__ == "__main__":
    unittest.main()
