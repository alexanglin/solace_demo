"""One bounded production migration console over the package-owned Alembic history."""

from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast, override
from unittest.mock import patch

import aerial_rescue_store.migrations.console as migration_console_module
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
from aerial_rescue_store.migration import CONNECTION_ATTRIBUTE, HEAD_REVISION
from aerial_rescue_store.migrations.console import MigrationRuntime, main, migrate
from aerial_rescue_store.settings import CONTAINER_HOST, DatabaseSettings
from alembic.config import Config
from sqlalchemy import Connection

pytestmark = [pytest.mark.unit]


def _bounds() -> EngineBounds:
    """Return every accepted store bound explicitly, as a composition root must."""
    return EngineBounds(
        pool_size=POOL_SIZE,
        pool_overflow=POOL_OVERFLOW,
        checkout_timeout_seconds=CHECKOUT_TIMEOUT_SECONDS,
        connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
        connect_retries=CONNECT_RETRIES,
        statement_timeout_milliseconds=STATEMENT_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=LOCK_TIMEOUT_MILLISECONDS,
        idle_in_transaction_timeout_milliseconds=(IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS),
        shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
    )


class FakeConnection:
    """Execute the supplied synchronous Alembic bridge against one stable object."""

    def __init__(self) -> None:
        """Retain one stable synchronous connection token."""
        self.sync_connection = cast("Connection", object())

    async def run_sync[ResultT](
        self,
        operation: Callable[[Connection], ResultT],
        /,
    ) -> ResultT:
        """Invoke the migration bridge exactly once."""
        return operation(self.sync_connection)


class FakeBegin:
    """Record entry and exit of one engine transaction."""

    def __init__(self, connection: FakeConnection) -> None:
        """Begin outside the transaction with the supplied connection."""
        self.connection = connection
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeConnection:
        """Record transaction entry and expose the connection."""
        self.entered = True
        return self.connection

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Record transaction exit regardless of the body outcome."""
        del exception_type, exception, traceback
        self.exited = True


class FakeEngine:
    """Expose only the transaction and disposal capabilities the console needs."""

    def __init__(self) -> None:
        """Begin undisposed with one reusable fake transaction."""
        self.connection = FakeConnection()
        self.transaction = FakeBegin(self.connection)
        self.disposed = False

    def begin(self) -> FakeBegin:
        return self.transaction

    async def dispose(self) -> None:
        self.disposed = True


class MigrationConsoleTests(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        """Create one private deploy tree holding a generated-style password."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.deploy = Path(self.temporary.name)
        secret = self.deploy / "secrets" / "postgres-password"
        secret.parent.mkdir()
        secret.write_text("database-password", encoding="ascii")

    async def test_migration_resolves_the_container_target_and_applies_head_once(self) -> None:
        # Arrange
        engine = FakeEngine()
        targets: list[tuple[DatabaseSettings, EngineBounds]] = []
        applications: list[tuple[object, str]] = []

        def create(target: DatabaseSettings, bounds: EngineBounds) -> FakeEngine:
            targets.append((target, bounds))
            return engine

        def upgrade(config: Config, revision: str) -> None:
            applications.append((config.attributes[CONNECTION_ATTRIBUTE], revision))

        runtime = MigrationRuntime(
            environment={"POSTGRES_USER": "aerial", "POSTGRES_DB": "missions"},
            deploy=self.deploy,
            host=CONTAINER_HOST,
            bounds=_bounds(),
            create_engine=create,
            upgrade=upgrade,
        )

        # Act
        await migrate(runtime)

        # Assert
        target, supplied_bounds = targets[0]
        self.assertEqual(
            (target.host, target.user, target.database, target.password),
            (CONTAINER_HOST, "aerial", "missions", "database-password"),
        )
        self.assertEqual(_bounds(), supplied_bounds)
        self.assertEqual([(engine.connection.sync_connection, HEAD_REVISION)], applications)
        self.assertTrue(engine.transaction.entered)
        self.assertTrue(engine.transaction.exited)
        self.assertTrue(engine.disposed)

    async def test_migration_failure_propagates_after_the_pool_is_disposed(self) -> None:
        # Arrange
        engine = FakeEngine()
        failure = RuntimeError("migration refused")

        def upgrade(_config: Config, _revision: str) -> None:
            raise failure

        runtime = MigrationRuntime(
            environment={"POSTGRES_USER": "aerial", "POSTGRES_DB": "missions"},
            deploy=self.deploy,
            host=CONTAINER_HOST,
            bounds=_bounds(),
            create_engine=lambda _target, _bounds: engine,
            upgrade=upgrade,
        )

        # Act
        with pytest.raises(RuntimeError) as raised:
            await migrate(runtime)

        # Assert
        self.assertIs(failure, raised.value)
        self.assertTrue(engine.transaction.exited)
        self.assertTrue(engine.disposed)


class MigrationMainTests(unittest.TestCase):
    def test_default_runtime_uses_the_container_host_and_explicit_bound_set(self) -> None:
        # Arrange
        deploy = "/run"
        environment = {migration_console_module.DEPLOY_DIRECTORY_SETTING: deploy}

        # Act
        with patch.dict(os.environ, environment, clear=True):
            runtime = migration_console_module.default_runtime()

        # Assert
        self.assertEqual(Path(deploy), runtime.deploy)
        self.assertEqual(CONTAINER_HOST, runtime.host)
        self.assertEqual(_bounds(), runtime.bounds)

    def test_main_runs_the_injected_migration_and_returns_success(self) -> None:
        # Arrange
        calls: list[MigrationRuntime] = []
        engine = FakeEngine()
        runtime = MigrationRuntime(
            environment={},
            deploy=Path("unused"),
            host=CONTAINER_HOST,
            bounds=_bounds(),
            create_engine=lambda _target, _bounds: engine,
            upgrade=lambda _config, _revision: None,
        )

        async def run(selected: MigrationRuntime) -> None:
            calls.append(selected)

        # Act
        status = main(runtime=runtime, runner=run)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual([runtime], calls)


if __name__ == "__main__":
    unittest.main()
