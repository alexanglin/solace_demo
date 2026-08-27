"""One-shot live migration composition, redacted configuration, and cleanup."""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_store import migrate
from aerial_rescue_store.migrate import (
    MigrationConfigError,
    MigrationConfigRefusal,
    MigrationConfiguration,
    configuration,
    migrate_to_head,
)

ENVIRONMENT = {
    "POSTGRES_USER": "aerial_rescue",
    "POSTGRES_DB": "aerial_rescue",
    "POSTGRES_PASSWORD_FILE": "/unused/postgres-password",
}


class MigrationConfigurationTests(unittest.TestCase):
    def test_configuration_uses_the_container_target_and_redacts_the_file_credential(self) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        secret = root / "postgres-password"
        secret.write_text("not-a-real-postgres-password\n", encoding="utf-8")
        environment = {**ENVIRONMENT, "POSTGRES_PASSWORD_FILE": str(secret)}

        # Act
        configured = configuration(environment)
        rendered = repr(configured)

        # Assert
        self.assertEqual("postgres", configured.database.host)
        self.assertNotIn("not-a-real-postgres-password", rendered)
        self.assertNotIn(str(secret), rendered)

    def test_every_required_value_and_missing_secret_refuses_with_only_its_setting_name(
        self,
    ) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        missing = root / "missing-password"
        cases = [
            ({**ENVIRONMENT, name: " "}, MigrationConfigRefusal.MISSING_SETTING, name)
            for name in ENVIRONMENT
        ]
        cases.append(
            (
                {**ENVIRONMENT, "POSTGRES_PASSWORD_FILE": str(missing)},
                MigrationConfigRefusal.MISSING_MATERIAL,
                "POSTGRES_PASSWORD_FILE",
            )
        )

        # Act
        captured: list[tuple[MigrationConfigRefusal, object]] = []
        for environment, _refusal, _value in cases:
            with pytest.raises(MigrationConfigError) as error:
                configuration(environment)
            captured.append((error.value.refusal, error.value.value))

        # Assert
        self.assertEqual(
            [(refusal, value) for _environment, refusal, value in cases],
            captured,
        )


@dataclass
class _Connection:
    callbacks: list[Callable[[object], None]] = field(default_factory=list)
    failure: RuntimeError | None = None

    async def run_sync(self, callback: Callable[[object], None]) -> None:
        if self.failure is not None:
            raise self.failure
        self.callbacks.append(callback)


@dataclass
class _Begin:
    connection: _Connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


@dataclass
class _Engine:
    connection: _Connection = field(default_factory=_Connection)

    def begin(self) -> _Begin:
        return _Begin(self.connection)


class MigrationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_shot_upgrade_closes_the_pool_after_registering_the_live_head_run(
        self,
    ) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        secret = root / "postgres-password"
        secret.write_text("not-a-real-postgres-password\n", encoding="utf-8")
        configured = configuration({**ENVIRONMENT, "POSTGRES_PASSWORD_FILE": str(secret)})
        engine = _Engine()
        closer = AsyncMock()

        # Act
        with (
            patch.object(migrate, "create_engine", return_value=engine),
            patch.object(migrate, "close", closer),
        ):
            await migrate_to_head(configured)

        # Assert
        self.assertEqual(1, len(engine.connection.callbacks))
        closer.assert_awaited_once_with(engine, configured.shutdown_grace_seconds)

    async def test_pool_cleanup_still_runs_when_opening_the_live_transaction_fails(self) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        secret = root / "postgres-password"
        secret.write_text("not-a-real-postgres-password\n", encoding="utf-8")
        configured = configuration({**ENVIRONMENT, "POSTGRES_PASSWORD_FILE": str(secret)})
        engine = _Engine()
        closer = AsyncMock()

        message = "synthetic migration failure"
        engine.connection.failure = RuntimeError(message)

        # Act
        with (
            patch.object(migrate, "create_engine", return_value=engine),
            patch.object(migrate, "close", closer),
            pytest.raises(RuntimeError, match="synthetic migration failure"),
        ):
            await migrate_to_head(configured)

        # Assert
        closer.assert_awaited_once_with(engine, configured.shutdown_grace_seconds)


class MigrationEntrypointTests(unittest.TestCase):
    def test_module_entrypoint_resolves_the_process_environment_and_runs_one_upgrade(self) -> None:
        # Arrange
        configured = cast("MigrationConfiguration", object())
        upgrade_awaitable = object()
        upgrades: list[MigrationConfiguration] = []

        def upgrade(value: MigrationConfiguration) -> object:
            upgrades.append(value)
            return upgrade_awaitable

        # Act
        with (
            patch.object(migrate, "configuration", return_value=configured) as configure,
            patch.object(migrate, "migrate_to_head", new=upgrade),
            patch("aerial_rescue_store.migrate.asyncio.run") as runner,
        ):
            migrate.main()

        # Assert
        configure.assert_called_once_with(os.environ)
        self.assertEqual([configured], upgrades)
        runner.assert_called_once_with(upgrade_awaitable)
