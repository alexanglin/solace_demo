"""Bounded one-shot migration process for the mission-control PostgreSQL schema."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, override

from alembic import command
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

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
from aerial_rescue_store.migration import HEAD_REVISION, live_config
from aerial_rescue_store.session import close
from aerial_rescue_store.settings import CONTAINER_HOST, DEFAULT_PORT, DatabaseSettings

POSTGRES_USER: Final = "POSTGRES_USER"
POSTGRES_DB: Final = "POSTGRES_DB"
CREDENTIAL_PATH_SETTING: Final = "POSTGRES_PASSWORD_FILE"
_REQUIRED: Final = (POSTGRES_USER, POSTGRES_DB, CREDENTIAL_PATH_SETTING)
_MAXIMUM_SECRET_BYTES: Final = 4096


class MigrationConfigRefusal(Enum):
    """Why the one-shot process cannot build its secret-safe target."""

    MISSING_SETTING = "required migration setting is absent or blank"
    MISSING_MATERIAL = "required migration secret material is unavailable"


class MigrationConfigError(ValueError):
    """A redacted configuration refusal that names only the setting involved."""

    def __init__(self, refusal: MigrationConfigRefusal, value: object) -> None:
        """Retain a structured reason and only the public setting name."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True, repr=False)
class MigrationConfiguration:
    """A redacted database target and the complete bounded engine policy."""

    database: DatabaseSettings
    bounds: EngineBounds

    @property
    def shutdown_grace_seconds(self) -> int:
        """Expose the one bound consumed during pool disposal."""
        return self.bounds.shutdown_grace_seconds

    @override
    def __repr__(self) -> str:
        """Render no credential or secret-file location."""
        return f"MigrationConfiguration(database={self.database!r}, bounds={self.bounds!r})"


def configuration(environment: Mapping[str, str]) -> MigrationConfiguration:
    """Resolve the fixed container target and bounded secret file without opening a database."""
    values: dict[str, str] = {}
    for name in _REQUIRED:
        value = environment.get(name, "").strip()
        if not value:
            raise MigrationConfigError(MigrationConfigRefusal.MISSING_SETTING, name)
        values[name] = value
    credential = _credential(Path(values[CREDENTIAL_PATH_SETTING]))
    return MigrationConfiguration(
        database=DatabaseSettings(
            host=CONTAINER_HOST,
            port=DEFAULT_PORT,
            user=values[POSTGRES_USER],
            database=values[POSTGRES_DB],
            password=credential,
        ),
        bounds=_bounds(),
    )


def _credential(path: Path) -> str:
    """Read one bounded regular secret while keeping its location out of refusals."""
    try:
        details = path.lstat()
    except OSError as invalid:
        raise MigrationConfigError(
            MigrationConfigRefusal.MISSING_MATERIAL, CREDENTIAL_PATH_SETTING
        ) from invalid
    if not stat.S_ISREG(details.st_mode) or details.st_size > _MAXIMUM_SECRET_BYTES:
        raise MigrationConfigError(MigrationConfigRefusal.MISSING_MATERIAL, CREDENTIAL_PATH_SETTING)
    try:
        raw = path.read_bytes()
        value = raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError) as invalid:
        raise MigrationConfigError(
            MigrationConfigRefusal.MISSING_MATERIAL, CREDENTIAL_PATH_SETTING
        ) from invalid
    if not value or b"\x00" in raw:
        raise MigrationConfigError(MigrationConfigRefusal.MISSING_MATERIAL, CREDENTIAL_PATH_SETTING)
    return value


def _bounds() -> EngineBounds:
    """Apply the store-owned pool and statement bounds without local alternatives."""
    return EngineBounds(
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


def _upgrade_connection(connection: Connection) -> None:
    """Apply only the package-owned linear history through the supplied live connection."""
    command.upgrade(live_config(connection), HEAD_REVISION)


async def _run_upgrade(connection: AsyncConnection) -> None:
    """Bridge the async composition root to Alembic's synchronous connection callback."""
    await connection.run_sync(_upgrade_connection)


async def migrate_to_head(configured: MigrationConfiguration) -> None:
    """Apply the current head once and dispose the pool on every exit path."""
    engine: AsyncEngine = create_engine(configured.database, configured.bounds)
    try:
        async with engine.begin() as connection:
            await _run_upgrade(connection)
    finally:
        await close(engine, configured.shutdown_grace_seconds)


def main() -> None:
    """Run the one-shot migration from the exact mission-control environment contract."""
    asyncio.run(migrate_to_head(configuration(os.environ)))


if __name__ == "__main__":
    main()
