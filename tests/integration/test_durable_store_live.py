"""The first revision applied to a real cluster, on a database this run creates and drops.

Everything `packages/store` could establish before this file was that Alembic *emits* a data
definition: `packages/store/tests/test_migration.py` runs the revision bodies against a
statement-emitting context and asserts the exact text, which is what earns the member's Tier 2
gate and is deliberately all it earns. Whether PostgreSQL accepts any of it, and whether the
constraints in that text are enforced rather than merely written, is this module's job and
only this module's job
([ADR-0086](../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)).

**The isolation strategy is a database named for the run**, created before each case and dropped
after it, never the operator's `POSTGRES_DB`. ADR-0086 rejects transactional rollback on three
independently sufficient grounds, and the first applies here directly: the migration *is* the
data-definition change under test, so a strategy that rolls everything back has nothing left to
observe. Creating a database costs an autocommit connection to the maintenance database, because
`CREATE DATABASE` cannot run inside a transaction.

**The refusal is a precondition rather than a convention.** `run_database_name` derives the name
and refuses when it equals the configured database, so "a probe never touches persistent mission
data" is executed rather than remembered.

This is the first module in the repository to open a database connection, and the first to run
`async` code at all.

Carries `integration` and `docker`, and deliberately **not** `broker`: a resource marker declares
what a test needs, this module needs no broker, and `docker` alone already excludes it from every
blocking stage (`tests/integration/AGENTS.md` section 3).

What a green run establishes: PostgreSQL accepts the first revision, stamps it, is unchanged by a
second application of the same head, enforces the two constraints the revision declares rather
than only carrying their text, and returns to an empty schema on the downgrade. What it does not
establish: transaction visibility, isolation behaviour, restart durability, pool cancellation,
concurrent races, migration from a *prior* revision, mismatch, or failure recovery. Each of those
needs its own case, and several need a second revision or a mechanism no record has selected yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import unittest
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, override
from uuid import uuid4

import pytest
from aerial_rescue_store import StoreError
from aerial_rescue_store.audit import AuditRecord, append
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
from aerial_rescue_store.engine import ISOLATION_LEVEL, create_engine
from aerial_rescue_store.migration import (
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    BASE_REVISION,
    HEAD_REVISION,
    live_config,
)
from aerial_rescue_store.session import create_session_factory, transaction
from aerial_rescue_store.settings import database_settings
from alembic import command
from sqlalchemy import (
    BigInteger,
    LargeBinary,
    String,
    column,
    insert,
    inspect,
    select,
    table,
    text,
)
from sqlalchemy.exc import DBAPIError, IntegrityError

if TYPE_CHECKING:
    from aerial_rescue_store.settings import DatabaseSettings
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import TextClause

pytestmark = [pytest.mark.integration, pytest.mark.docker]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = REPOSITORY_ROOT / "deploy"

MAINTENANCE_DATABASE: Final = "postgres"
"""The database the autocommit connection addresses, because `CREATE DATABASE` needs one."""

RUN_DATABASE_PREFIX: Final = "aerial_rescue_probe_"
"""Names every database this module creates, so a leak from an interrupted run is visible."""

VERSION_TABLE: Final = "alembic_version"
FIRST_REVISION: Final = "0001_audit_log"

APPLIED_TABLES: Final = tuple(sorted((AUDIT_RECORD_TABLE, AUDIT_SEQUENCE_TABLE, VERSION_TABLE)))
"""Every table a migrated database holds: the revision's two, and Alembic's own."""

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
"""ADR-0090's bounds, unmodified. A probe that widened one would be measuring something else."""

MISSION: Final = "m-store-probe"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"
OCCURRED_AT: Final = "2026-08-23T12:00:00.000Z"
PAYLOAD: Final = b'{"probe":true}'
CORRELATION: Final = "c-store-probe"

CREATE: Final = "CREATE"
DROP: Final = "DROP"

SEQUENCE_ROWS: Final = table(
    AUDIT_SEQUENCE_TABLE,
    column("mission_id", String),
    column("next_ordinal", BigInteger),
)
RECORD_ROWS: Final = table(
    AUDIT_RECORD_TABLE,
    column("mission_id", String),
    column("ordinal", BigInteger),
    column("kind", String),
    column("occurred_at", String),
    column("payload", LargeBinary),
    column("correlation_id", String),
    column("traceparent", String),
)
STAMPED_REVISION: Final = select(column("version_num", String)).select_from(table(VERSION_TABLE))
"""Every statement below is a typed expression, so no table name is interpolated into SQL."""

MISSION_ORDINALS: Final = (
    select(RECORD_ROWS.c.ordinal, RECORD_ROWS.c.kind)
    .where(RECORD_ROWS.c.mission_id == MISSION)
    .order_by(RECORD_ROWS.c.ordinal)
)

EXPECTED_BOUNDS: Final = ("5s", "2s", "15s")
"""What ADR-0090's three server-side milliseconds normalise to when PostgreSQL renders them."""

SHOWN_BOUNDS: Final = (
    text("SHOW statement_timeout"),
    text("SHOW lock_timeout"),
    text("SHOW idle_in_transaction_session_timeout"),
)
SHOWN_ISOLATION: Final = text("SHOW transaction_isolation")
LONGER_THAN_THE_STATEMENT_BOUND: Final = text("SELECT pg_sleep(6)")
STATEMENT_TIMEOUT_MESSAGE: Final = "canceling statement due to statement timeout"

HELD_WINDOW_SECONDS: Final = 0.5
"""How long the waiting appender is watched. Far below ADR-0090's two-second lock wait, so a
blocked appender is still blocked when the window ends rather than already refused."""


class AbandonedError(Exception):
    """Raised inside a transaction to abandon it, so the rollback is the caller's own decision."""


def _record(mission: str, kind: str) -> AuditRecord:
    """Return one synthetic audit record for this probe."""
    return AuditRecord(
        mission_id=mission,
        kind=kind,
        occurred_at=OCCURRED_AT,
        payload=PAYLOAD,
        correlation_id=CORRELATION,
        causation_id=None,
        traceparent=TRACEPARENT,
    )


class ProbeRefusal(Enum):
    """Why this probe declines to run at all."""

    PERSISTENT_TARGET = (
        "the derived run database is the configured POSTGRES_DB, and a probe never creates, "
        "migrates, or drops the operator's own mission database"
    )


class ProbeError(StoreError):
    """A target this probe refuses, carrying the refusal as structured data."""


def run_database_name(configured: str, discriminator: str) -> str:
    """Return the database this run creates, refusing the one the operator's data lives in.

    Args:
        configured: The resolved `POSTGRES_DB`, which this run must never address.
        discriminator: The per-run value that makes the name unique and traceable.

    Returns:
        The run database's name.

    Raises:
        ProbeError: With `PERSISTENT_TARGET` when the derived name is the configured one.
            ADR-0086 requires this comparison to be executed rather than remembered, so it is a
            refusal at the point of derivation and not a rule written down somewhere.
    """
    name = f"{RUN_DATABASE_PREFIX}{discriminator}"
    if name == configured:
        raise ProbeError(ProbeRefusal.PERSISTENT_TARGET, name)
    return name


def probe_target() -> DatabaseSettings:
    """Return this run's target: the configured cluster and credential, a database of its own.

    Reads `os.environ` so the comparison ADR-0086 requires is made against what is actually
    configured rather than against a constant that would silently diverge from an edited `.env`.
    A missing setting is `SettingsError(MISSING_SETTING)` from `database_settings`, which is the
    intended fail-closed outcome rather than a guess at the operator's database.
    """
    configured = database_settings(os.environ, DEPLOY)
    return replace(configured, database=run_database_name(configured.database, uuid4().hex))


async def _on_maintenance_database(target: DatabaseSettings, action: str) -> None:
    """Create or drop `target`'s database from outside it, with no transaction open.

    Neither statement can run inside a transaction, so this opens an autocommit connection to
    the maintenance database -- one more privileged step than a rollback strategy would need,
    which ADR-0086 names as a cost of this strategy. The name is quoted by the very dialect
    that executes the statement, so nothing here hand-quotes an identifier into SQL text.
    """
    engine = create_engine(replace(target, database=MAINTENANCE_DATABASE), BOUNDS)
    try:
        quoted = engine.dialect.identifier_preparer.quote(target.database)
        autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with autocommit.connect() as connection:
            await connection.execute(text(f"{action} DATABASE {quoted}"))
    finally:
        await engine.dispose()


async def _apply(engine: AsyncEngine, revision: str) -> None:
    """Apply the history through one connection, up or down, and commit."""
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: command.upgrade(live_config(sync), revision))


async def _downgrade(engine: AsyncEngine, revision: str) -> None:
    """Reverse the history through one connection and commit."""
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: command.downgrade(live_config(sync), revision))


async def _table_names(engine: AsyncEngine) -> tuple[str, ...]:
    """Return every table PostgreSQL reports in the run database, sorted."""
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    return tuple(sorted(names))


async def _column_names(engine: AsyncEngine, table: str) -> tuple[str, ...]:
    """Return every column PostgreSQL reports for `table`, sorted."""
    async with engine.connect() as connection:
        columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(table))
    return tuple(sorted(str(column["name"]) for column in columns))


async def _stamped_revision(engine: AsyncEngine) -> str | None:
    """Return the revision the database says it is at, or `None` if it is at none."""
    async with engine.connect() as connection:
        result = await connection.execute(STAMPED_REVISION)
    stamped = result.scalar_one_or_none()
    return None if stamped is None else str(stamped)


class RunDatabaseNameTests(unittest.TestCase):
    def test_a_run_database_is_named_for_the_run_that_creates_it(self) -> None:
        # Arrange
        discriminator = "0123456789abcdef"

        # Act
        name = run_database_name("aerial_rescue", discriminator)

        # Assert
        self.assertEqual(f"{RUN_DATABASE_PREFIX}{discriminator}", name)

    def test_the_probe_refuses_a_derived_name_that_is_the_configured_database(self) -> None:
        # Arrange
        discriminator = "0123456789abcdef"
        collision = f"{RUN_DATABASE_PREFIX}{discriminator}"

        # Act
        with pytest.raises(ProbeError) as refused:
            run_database_name(collision, discriminator)

        # Assert
        self.assertEqual(ProbeRefusal.PERSISTENT_TARGET, refused.value.refusal)


class FirstRevisionLiveTests(unittest.IsolatedAsyncioTestCase):
    """Each case gets an empty database of its own, created here and dropped in teardown."""

    target: DatabaseSettings
    engine: AsyncEngine

    @override
    async def asyncSetUp(self) -> None:
        """Create this case's database and open an engine on it. No schema is applied here."""
        self.target = probe_target()
        await _on_maintenance_database(self.target, CREATE)
        self.engine = create_engine(self.target, BOUNDS)

    @override
    async def asyncTearDown(self) -> None:
        """Close every connection, then drop the database this case created."""
        await self.engine.dispose()
        await _on_maintenance_database(self.target, DROP)

    async def test_postgresql_accepts_the_first_revision(self) -> None:
        # Arrange
        before = await _table_names(self.engine)

        # Act
        await _apply(self.engine, HEAD_REVISION)

        # Assert
        self.assertEqual(
            ((), APPLIED_TABLES),
            (before, await _table_names(self.engine)),
        )

    async def test_the_applied_record_table_carries_the_columns_the_revision_declares(
        self,
    ) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)

        # Act
        columns = await _column_names(self.engine, AUDIT_RECORD_TABLE)

        # Assert
        self.assertEqual(
            (
                "causation_id",
                "correlation_id",
                "kind",
                "mission_id",
                "occurred_at",
                "ordinal",
                "payload",
                "traceparent",
            ),
            columns,
        )

    async def test_the_database_records_the_revision_it_was_brought_to(self) -> None:
        # Arrange
        before = await _table_names(self.engine)

        # Act
        await _apply(self.engine, HEAD_REVISION)

        # Assert
        self.assertEqual(((), FIRST_REVISION), (before, await _stamped_revision(self.engine)))

    async def test_applying_the_same_head_a_second_time_changes_nothing(self) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)
        first = (await _table_names(self.engine), await _stamped_revision(self.engine))

        # Act
        await _apply(self.engine, HEAD_REVISION)

        # Assert
        self.assertEqual(
            first, (await _table_names(self.engine), await _stamped_revision(self.engine))
        )

    async def test_the_sequence_check_constraint_is_enforced_and_not_merely_written(self) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)
        forbidden = insert(SEQUENCE_ROWS).values(mission_id=MISSION, next_ordinal=0)

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(forbidden)

        # Assert
        self.assertIn("ck_audit_sequence_ordinal_positive", str(refused.value.orig))

    async def test_a_mission_cannot_have_two_records_at_the_same_ordinal(self) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)
        first = insert(RECORD_ROWS).values(
            mission_id=MISSION,
            ordinal=1,
            kind="probe",
            occurred_at=OCCURRED_AT,
            payload=PAYLOAD,
            correlation_id=CORRELATION,
            traceparent=TRACEPARENT,
        )
        async with self.engine.begin() as connection:
            await connection.execute(first)

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(first)

        # Assert
        self.assertIn("pk_audit_record", str(refused.value.orig))

    async def test_the_downgrade_returns_the_database_to_the_state_it_was_created_in(
        self,
    ) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)

        # Act
        await _downgrade(self.engine, BASE_REVISION)

        # Assert
        self.assertEqual(
            ((VERSION_TABLE,), None),
            (await _table_names(self.engine), await _stamped_revision(self.engine)),
        )


async def _append_and_hold(
    engine: AsyncEngine, mission: str, took: asyncio.Future[int], release: asyncio.Event
) -> None:
    """Append, publish the ordinal taken, and keep the transaction open until released."""
    async with transaction(create_session_factory(engine)) as session:
        took.set_result(await append(session, _record(mission, "holder")))
        await release.wait()


async def _append_once(engine: AsyncEngine, mission: str, kind: str) -> int:
    """Append one record in its own committed transaction and return its ordinal."""
    async with transaction(create_session_factory(engine)) as session:
        return await append(session, _record(mission, kind))


async def _abandon_an_append(engine: AsyncEngine, mission: str) -> None:
    """Append, then abandon the transaction, so the rollback is what ends it."""
    with contextlib.suppress(AbandonedError):
        async with transaction(create_session_factory(engine)) as session:
            await append(session, _record(mission, "abandoned"))
            raise AbandonedError


@dataclass(frozen=True)
class Race:
    """What two appenders for one mission did, and whether the second had to wait."""

    first: int
    second: int
    second_waited: bool


async def _two_appenders_on_one_mission(engine: AsyncEngine, mission: str) -> Race:
    """Run the race ADR-0088's ordering claim is about, and observe the wait itself.

    The first appender takes its ordinal and holds the transaction open. The second is then
    started and watched for a window far below the lock wait: if it is still unfinished when the
    window ends, the row lock is what is holding it, not the scheduler. Only then is the first
    released, so the second's ordinal is issued after the first commits rather than beside it.
    """
    took: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    release = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        _ = group.create_task(_append_and_hold(engine, mission, took, release))
        first = await took
        waiter = group.create_task(_append_once(engine, mission, "waiter"))
        finished, _ = await asyncio.wait({waiter}, timeout=HELD_WINDOW_SECONDS)
        release.set()
    return Race(first=first, second=waiter.result(), second_waited=not finished)


async def _shown(engine: AsyncEngine, statements: tuple[TextClause, ...]) -> tuple[str, ...]:
    """Return what the server reports for each setting, on one session from this engine."""
    async with engine.connect() as connection:
        shown = [str(await connection.scalar(statement)) for statement in statements]
    return tuple(shown)


async def _ordinals(engine: AsyncEngine) -> tuple[tuple[int, str], ...]:
    """Return every record this probe's mission holds, as ordinal and kind, in ordinal order."""
    async with engine.connect() as connection:
        result = await connection.execute(MISSION_ORDINALS)
    return tuple((int(row[0]), str(row[1])) for row in result.all())


class AuditAppendLiveTests(unittest.IsolatedAsyncioTestCase):
    """Each case gets a migrated database of its own, created here and dropped in teardown."""

    target: DatabaseSettings
    engine: AsyncEngine

    @override
    async def asyncSetUp(self) -> None:
        """Create this case's database, open an engine on it, and bring it to head."""
        self.target = probe_target()
        await _on_maintenance_database(self.target, CREATE)
        self.engine = create_engine(self.target, BOUNDS)
        await _apply(self.engine, HEAD_REVISION)

    @override
    async def asyncTearDown(self) -> None:
        """Close every connection, then drop the database this case created."""
        await self.engine.dispose()
        await _on_maintenance_database(self.target, DROP)

    async def test_every_server_side_bound_reaches_a_session_rather_than_only_the_driver(
        self,
    ) -> None:
        # Arrange
        statements = SHOWN_BOUNDS

        # Act
        shown = await _shown(self.engine, statements)

        # Assert
        self.assertEqual(EXPECTED_BOUNDS, shown)

    async def test_the_isolation_level_the_engine_states_is_the_one_the_server_reports(
        self,
    ) -> None:
        # Arrange
        statements = (SHOWN_ISOLATION,)

        # Act
        shown = await _shown(self.engine, statements)

        # Assert
        self.assertEqual((ISOLATION_LEVEL.lower(),), shown)

    async def test_a_statement_past_the_bound_is_cancelled_by_the_server(self) -> None:
        # Arrange
        statement = LONGER_THAN_THE_STATEMENT_BOUND

        # Act
        with pytest.raises(DBAPIError) as cancelled:
            async with self.engine.connect() as connection:
                await connection.execute(statement)

        # Assert
        self.assertIn(STATEMENT_TIMEOUT_MESSAGE, str(cancelled.value.orig))

    async def test_a_committed_record_is_visible_to_a_session_that_did_not_write_it(self) -> None:
        # Arrange
        before = await _ordinals(self.engine)

        # Act
        ordinal = await _append_once(self.engine, MISSION, "committed")

        # Assert
        self.assertEqual(
            ((), 1, ((1, "committed"),)), (before, ordinal, await _ordinals(self.engine))
        )

    async def test_an_abandoned_append_leaves_neither_a_record_nor_a_gap(self) -> None:
        # Arrange
        await _abandon_an_append(self.engine, MISSION)

        # Act
        ordinal = await _append_once(self.engine, MISSION, "after")

        # Assert
        self.assertEqual((1, ((1, "after"),)), (ordinal, await _ordinals(self.engine)))

    async def test_two_appenders_for_one_mission_are_ordered_by_the_lock_the_first_holds(
        self,
    ) -> None:
        # Arrange
        mission = MISSION

        # Act
        race = await _two_appenders_on_one_mission(self.engine, mission)

        # Assert
        self.assertEqual(
            (1, 2, True, ((1, "holder"), (2, "waiter"))),
            (race.first, race.second, race.second_waited, await _ordinals(self.engine)),
        )

    async def test_appending_never_alters_a_record_already_written(self) -> None:
        # Arrange
        first = await _append_once(self.engine, MISSION, "first")

        # Act
        second = await _append_once(self.engine, MISSION, "second")

        # Assert
        self.assertEqual(
            (1, 2, ((1, "first"), (2, "second"))),
            (first, second, await _ordinals(self.engine)),
        )


if __name__ == "__main__":
    unittest.main()
