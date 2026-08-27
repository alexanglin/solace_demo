"""Bounded production composition for applying the package-owned Alembic migration history.

This module is the only executable migration root.  It resolves the generated PostgreSQL
credential, constructs the selected async SQLAlchemy engine, applies Alembic ``head`` through
that engine's synchronous bridge, and always disposes the pool.  It never creates metadata,
issues opportunistic DDL, or imports ``asyncpg`` directly.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from aerial_rescue_store.bounds import (
    CHECKOUT_TIMEOUT_SECONDS,
    CONNECT_RETRIES,
    CONNECT_TIMEOUT_SECONDS,
    IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    LOCK_TIMEOUT_MILLISECONDS,
    MIGRATION_WAIT_SECONDS,
    POOL_OVERFLOW,
    POOL_SIZE,
    SHUTDOWN_GRACE_SECONDS,
    STATEMENT_TIMEOUT_MILLISECONDS,
    EngineBounds,
)
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.migration import HEAD_REVISION, live_config
from aerial_rescue_store.session import close
from aerial_rescue_store.settings import (
    CONTAINER_HOST,
    DatabaseSettings,
    database_settings,
)
from alembic import command
from alembic.config import Config

if TYPE_CHECKING:
    from sqlalchemy import Connection

DEFAULT_DEPLOY_DIRECTORY = "deploy"
DEPLOY_DIRECTORY_SETTING = "AERIAL_RESCUE_DEPLOY_DIR"


class MigrationConnection(Protocol):
    """The SQLAlchemy async-to-sync bridge used by Alembic."""

    async def run_sync[ResultT](
        self,
        operation: Callable[[Connection], ResultT],
        /,
    ) -> ResultT:
        """Run one operation against this connection's synchronous proxy."""


class MigrationEngine(Protocol):
    """The transaction and disposal capabilities needed by this root."""

    def begin(self) -> AbstractAsyncContextManager[MigrationConnection]:
        """Open one transaction that commits only after Alembic succeeds."""

    async def dispose(self) -> None:
        """Release every pooled connection."""


EngineFactory = Callable[[DatabaseSettings, EngineBounds], MigrationEngine]
Upgrade = Callable[[Config, str], None]
Runner = Callable[["MigrationRuntime"], Coroutine[object, object, None]]


@dataclass(frozen=True, slots=True)
class MigrationRuntime:
    """Every setting and external operation the migration process uses."""

    environment: Mapping[str, str]
    deploy: Path
    host: str
    bounds: EngineBounds
    create_engine: EngineFactory
    upgrade: Upgrade


def _production_bounds() -> EngineBounds:
    """Construct the complete accepted bound set without relying on driver defaults."""
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


def default_runtime() -> MigrationRuntime:
    """Resolve the container migration runtime without opening a connection."""
    deploy = Path(os.environ.get(DEPLOY_DIRECTORY_SETTING, DEFAULT_DEPLOY_DIRECTORY))
    return MigrationRuntime(
        environment=os.environ,
        deploy=deploy,
        host=CONTAINER_HOST,
        bounds=_production_bounds(),
        create_engine=create_engine,
        upgrade=command.upgrade,
    )


async def migrate(runtime: MigrationRuntime) -> None:
    """Apply Alembic ``head`` within the migration bound and always close the pool."""
    target = database_settings(runtime.environment, runtime.deploy, host=runtime.host)
    engine = runtime.create_engine(target, runtime.bounds)
    try:
        async with asyncio.timeout(MIGRATION_WAIT_SECONDS):
            async with engine.begin() as connection:

                def apply(sync_connection: Connection) -> None:
                    runtime.upgrade(live_config(sync_connection), HEAD_REVISION)

                await connection.run_sync(apply)
    finally:
        await close(engine, runtime.bounds.shutdown_grace_seconds)


def main(
    runtime: MigrationRuntime | None = None,
    *,
    runner: Runner | None = None,
) -> int:
    """Apply the schema and return success only after the bounded run completes."""
    selected_runtime = default_runtime() if runtime is None else runtime
    selected_runner = migrate if runner is None else runner
    asyncio.run(selected_runner(selected_runtime))
    return 0
