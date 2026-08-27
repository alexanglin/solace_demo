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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Final, cast, override
from unittest.mock import patch
from uuid import uuid4

import pytest
from aerial_rescue_dashboard_api.boundary.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.orchestration import OperationCoordinator
from aerial_rescue_dashboard_api.ports import (
    CurrentRun,
    MutationKind,
    MutationProposal,
    ReplayPreparation,
    RunMode,
    ScenarioRunStatus,
)
from aerial_rescue_dashboard_api.store_adapter import SqlStore
from aerial_rescue_domain.approvals import (
    Approval,
    ApprovalError,
    ApprovalState,
    ClockReading,
    Proposal,
    consume,
    proposal_digest,
)
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store import StoreError
from aerial_rescue_store.approvals import (
    StoredApproval,
    StoredApprovalError,
    load_for_update,
    persist_consumed,
    record,
)
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
from aerial_rescue_store.dashboard.events import (
    BrokerEvent,
    BrokerEventOutcome,
    EventSession,
    append_broker_event,
)
from aerial_rescue_store.dashboard.runs import (
    DashboardMission,
    DashboardRun,
    create_mission,
    create_run,
    run_by_identity,
)
from aerial_rescue_store.dashboard.runs import (
    RunMode as StoredRunMode,
)
from aerial_rescue_store.engine import ISOLATION_LEVEL, create_engine
from aerial_rescue_store.idempotency import (
    StoredClaim,
    StoredClaimError,
    claim,
    record_result,
)
from aerial_rescue_store.migration import (
    APPLICATION_OUTBOX_TABLE,
    APPROVAL_BINDING_TABLE,
    APPROVAL_TABLE,
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    BASE_REVISION,
    BROKER_INBOX_TABLE,
    BROKER_REFUSAL_TABLE,
    COMMAND_OUTBOX_TABLE,
    COMMAND_PROGRESS_TABLE,
    DASHBOARD_BROKER_EVENT_TABLE,
    DASHBOARD_BROKER_SOURCE_TABLE,
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_OPERATION_TABLE,
    DASHBOARD_RUN_TABLE,
    DRONE_COMMAND_EFFECT_TABLE,
    DRONE_COMMAND_RECEIPT_TABLE,
    DRONE_STREAM_STATE_TABLE,
    EVIDENCE_DECISION_TABLE,
    EVIDENCE_ITEM_TABLE,
    HEAD_REVISION,
    IDEMPOTENCY_CLAIM_TABLE,
    PENDING_INVOCATION_TABLE,
    PROPOSAL_TABLE,
    SOURCE_EVENT_TABLE,
    SOURCE_EVIDENCE_ITEM_TABLE,
    live_config,
    migration_config,
    revisions,
)
from aerial_rescue_store.outbox import (
    MAXIMUM_UNCONFIRMED_RECORDS,
    StagedCommand,
    StagedCommandError,
    record_publication,
    stage,
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
    update,
)
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.broker_live_support import DEPLOY_ROOT as DEPLOY
from tests.broker_live_support import REPOSITORY_ROOT

if TYPE_CHECKING:
    from aerial_rescue_store.settings import DatabaseSettings
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import TextClause

pytestmark = [pytest.mark.integration, pytest.mark.docker]

MAINTENANCE_DATABASE: Final = "postgres"
"""The database the autocommit connection addresses, because `CREATE DATABASE` needs one."""

RUN_DATABASE_PREFIX: Final = "aerial_rescue_probe_"
"""Names every database this module creates, so a leak from an interrupted run is visible."""

VERSION_TABLE: Final = "alembic_version"
FIRST_REVISION: Final = "0001_audit_log"
SECOND_REVISION: Final = "0002_approval"
THIRD_REVISION: Final = "0003_idempotency"
FOURTH_REVISION: Final = "0004_command_outbox"
FIFTH_REVISION: Final = "0005_dashboard_runtime"
SIXTH_REVISION: Final = "0006_application_processing"
SEVENTH_REVISION: Final = "0007_durable_fleet_processing"
EIGHTH_REVISION: Final = "0008_command_gateway_authority"
NINTH_REVISION: Final = "0009_broker_refusal"
TENTH_REVISION: Final = "0010_dashboard_idempotency"

_FIRST_TABLES: Final = (AUDIT_RECORD_TABLE, AUDIT_SEQUENCE_TABLE, VERSION_TABLE)
_SECOND_TABLES: Final = (*_FIRST_TABLES, APPROVAL_TABLE)
_THIRD_TABLES: Final = (*_SECOND_TABLES, IDEMPOTENCY_CLAIM_TABLE)
_FOURTH_TABLES: Final = (*_THIRD_TABLES, COMMAND_OUTBOX_TABLE)
_FIFTH_TABLES: Final = (
    *_FOURTH_TABLES,
    DASHBOARD_BROKER_EVENT_TABLE,
    DASHBOARD_BROKER_SOURCE_TABLE,
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_OPERATION_TABLE,
    DASHBOARD_RUN_TABLE,
)
_SIXTH_TABLES: Final = (
    *_FIFTH_TABLES,
    BROKER_INBOX_TABLE,
    SOURCE_EVENT_TABLE,
    SOURCE_EVIDENCE_ITEM_TABLE,
    APPLICATION_OUTBOX_TABLE,
    PROPOSAL_TABLE,
    EVIDENCE_ITEM_TABLE,
    EVIDENCE_DECISION_TABLE,
    COMMAND_PROGRESS_TABLE,
    DRONE_COMMAND_RECEIPT_TABLE,
)
_SEVENTH_TABLES: Final = (
    *_SIXTH_TABLES,
    DRONE_STREAM_STATE_TABLE,
    DRONE_COMMAND_EFFECT_TABLE,
)
_EIGHTH_TABLES: Final = (
    *_SEVENTH_TABLES,
    PENDING_INVOCATION_TABLE,
    APPROVAL_BINDING_TABLE,
)
_NINTH_TABLES: Final = (*_EIGHTH_TABLES, BROKER_REFUSAL_TABLE)
_TENTH_TABLES: Final = _NINTH_TABLES

HISTORY: Final = (
    (FIRST_REVISION, tuple(sorted(_FIRST_TABLES))),
    (SECOND_REVISION, tuple(sorted(_SECOND_TABLES))),
    (THIRD_REVISION, tuple(sorted(_THIRD_TABLES))),
    (FOURTH_REVISION, tuple(sorted(_FOURTH_TABLES))),
    (FIFTH_REVISION, tuple(sorted(_FIFTH_TABLES))),
    (SIXTH_REVISION, tuple(sorted(_SIXTH_TABLES))),
    (SEVENTH_REVISION, tuple(sorted(_SEVENTH_TABLES))),
    (EIGHTH_REVISION, tuple(sorted(_EIGHTH_TABLES))),
    (NINTH_REVISION, tuple(sorted(_NINTH_TABLES))),
    (TENTH_REVISION, tuple(sorted(_TENTH_TABLES))),
)
"""Every step and what the database holds after it. Literals, so a new revision is noticed here
too (`tests/AGENTS.md` section 4), and a list rather than a head because a one-revision tree could
express neither a path nor a step back along one."""

HEAD_REVISION_ID: Final = TENTH_REVISION
APPLIED_TABLES: Final = tuple(sorted(_TENTH_TABLES))
"""Every table a migrated database holds, including Alembic's own version table."""

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

SHOWN_DEADLOCK_DETECTION: Final = (text("SHOW deadlock_timeout"),)
EXPECTED_DEADLOCK_DETECTION: Final = ("1s",)
"""What the cluster renders its own ``deadlock_timeout`` as. The store never sets it: ADR-0090
derives the lock wait from it instead, so ``SERVER_DEADLOCK_TIMEOUT_MILLISECONDS`` is a reading of
this cluster rather than an assumption about it."""
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


async def _applied(engine: AsyncEngine, revision: str) -> tuple[str | None, tuple[str, ...]]:
    """Bring the database up one revision and report what it says it is, and what it holds."""
    await _apply(engine, revision)
    return (await _stamped_revision(engine), await _table_names(engine))


async def _stepped_back(engine: AsyncEngine, revision: str) -> tuple[str | None, tuple[str, ...]]:
    """Take the database down one revision and report what it says it is, and what it holds."""
    await _downgrade(engine, revision)
    return (await _stamped_revision(engine), await _table_names(engine))


async def _stamped_revision(engine: AsyncEngine) -> str | None:
    """Return the revision the database says it is at, or `None` if it is at none."""
    async with engine.connect() as connection:
        result = await connection.execute(STAMPED_REVISION)
    stamped = result.scalar_one_or_none()
    return None if stamped is None else str(stamped)


class RunDatabaseNameTests(unittest.TestCase):
    def test_the_live_walk_inventory_covers_the_complete_package_history(self) -> None:
        # Arrange
        expected = revisions(migration_config("postgresql+asyncpg://offline"))

        # Act
        walked = tuple(revision for revision, _tables in reversed(HISTORY))

        # Assert
        self.assertEqual(expected, walked)

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
        self.assertEqual(((), HEAD_REVISION_ID), (before, await _stamped_revision(self.engine)))

    async def test_the_history_applies_one_revision_at_a_time(self) -> None:
        # Arrange
        expected = HISTORY

        # Act
        observed = tuple([await _applied(self.engine, revision) for revision, _ in expected])

        # Assert
        self.assertEqual(expected, observed)

    async def test_each_step_back_leaves_the_revision_below_it_intact(self) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)
        expected = HISTORY[:-1][::-1]

        # Act
        observed = tuple([await _stepped_back(self.engine, revision) for revision, _ in expected])

        # Assert
        self.assertEqual(expected, observed)

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


DASHBOARD_SCENARIO_FIXTURE: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/dashboard/scenario-catalog/baseline.json"
)
DASHBOARD_START_KEY: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
DASHBOARD_RESET_KEY: Final = "4984a66b-ff04-4128-94ea-24578dc54851"
DASHBOARD_RECOVERY_KEY: Final = "ada6dd4f-b742-447c-8479-9778919d993b"
DASHBOARD_START_DIGEST: Final = "aa" * 32
DASHBOARD_RESET_DIGEST: Final = "bb" * 32
DASHBOARD_RECOVERY_DIGEST: Final = "cc" * 32
DASHBOARD_EVENT_DIGEST: Final = "dd" * 32
UNEXPECTED_REPLAY: Final = "live integration reached replay readiness"
MISSING_CURRENT_RUN: Final = "dashboard integration expected a current run"


@dataclass
class _DashboardIdentifiers:
    """Generate deterministic integration identities without process-global state."""

    counts: dict[str, int] = field(default_factory=dict)

    def new(self, kind: str) -> str:
        """Return the next stable identity for one namespace."""
        count = self.counts.get(kind, 0) + 1
        self.counts[kind] = count
        return f"{kind}-test-{count:04d}"


@dataclass
class _DashboardScenario:
    """Record private control while the real store owns durable behavior."""

    catalog_bytes: bytes
    starts: list[tuple[str, str]] = field(default_factory=list)
    cancels: list[tuple[str, str, float]] = field(default_factory=list)
    status_mission_id: str = "mission-test-0001"
    status_run_id: str = "run-test-0001"

    async def readiness(self) -> tuple[str, ...]:
        """Return no private-control readiness refusal."""
        return ()

    async def catalog(self) -> bytes:
        """Return exact committed catalog bytes."""
        return self.catalog_bytes

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Accept and record one stable live run identity."""
        self.starts.append((mission_id, run_id))
        return ScenarioRunStatus(scenario_id, scenario_revision, mission_id, run_id, "SEARCHING")

    async def status(self, run_id: str) -> ScenarioRunStatus:
        """Reconcile the stable run without repeating start."""
        if run_id != self.status_run_id:
            raise AssertionError(run_id)
        return ScenarioRunStatus(
            "wilderness-missing-person",
            1,
            self.status_mission_id,
            self.status_run_id,
            "SEARCHING",
        )

    async def cancel(self, mission_id: str, run_id: str, timeout: float) -> ScenarioRunStatus:
        """Establish cancellation and record its shared bound."""
        self.cancels.append((mission_id, run_id, timeout))
        return ScenarioRunStatus("wilderness-missing-person", 1, mission_id, run_id, "ABORTED")

    async def recover(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Fail if the known-run integration unexpectedly takes lost-run recovery."""
        raise AssertionError((scenario_id, scenario_revision, mission_id, run_id))


class _RefusingReplay:
    """Fail immediately if this live-only integration crosses into replay."""

    async def readiness(self) -> tuple[str, ...]:
        """Refuse an unexpected replay readiness read."""
        raise AssertionError(UNEXPECTED_REPLAY)

    async def prepare(self, scenario_id: str, scenario_revision: int) -> ReplayPreparation:
        """Refuse an unexpected replay preparation."""
        raise AssertionError((scenario_id, scenario_revision))

    async def bundle(self, session_id: str) -> bytes | None:
        """Refuse an unexpected replay bundle read."""
        raise AssertionError(session_id)


def _required_current(run: CurrentRun | None) -> CurrentRun:
    """Narrow a required durable pointer without hiding a missing-run failure."""
    if run is None:
        raise AssertionError(MISSING_CURRENT_RUN)
    return run


async def _dashboard_runtime(
    engine: AsyncEngine,
) -> tuple[SqlStore, _DashboardScenario, OperationCoordinator]:
    """Migrate and compose dashboard orchestration over the caller's real store."""
    await _apply(engine, HEAD_REVISION)
    store = SqlStore(
        create_session_factory(engine),
        engine,
        SHUTDOWN_GRACE_SECONDS,
    )
    scenario = _DashboardScenario(DASHBOARD_SCENARIO_FIXTURE.read_bytes())
    coordinator = OperationCoordinator(
        store,
        scenario,
        _RefusingReplay(),
        _DashboardIdentifiers(),
    )
    return store, scenario, coordinator


class DashboardRuntimeLiveTests(unittest.IsolatedAsyncioTestCase):
    """Prove revision 0005 and dashboard recovery on a real disposable database."""

    target: DatabaseSettings
    engine: AsyncEngine

    @override
    async def asyncSetUp(self) -> None:
        """Create one empty database without applying a revision implicitly."""
        self.target = probe_target()
        await _on_maintenance_database(self.target, CREATE)
        self.engine = create_engine(self.target, BOUNDS)

    @override
    async def asyncTearDown(self) -> None:
        """Close every connection before dropping only this test's database."""
        await self.engine.dispose()
        await _on_maintenance_database(self.target, DROP)

    async def test_revision_0005_upgrades_and_downgrades_as_one_additive_step(self) -> None:
        # Arrange
        await _apply(self.engine, FOURTH_REVISION)
        before = (await _stamped_revision(self.engine), await _table_names(self.engine))

        # Act
        await _apply(self.engine, FIFTH_REVISION)
        upgraded = (await _stamped_revision(self.engine), await _table_names(self.engine))
        await _downgrade(self.engine, FOURTH_REVISION)
        downgraded = (await _stamped_revision(self.engine), await _table_names(self.engine))

        # Assert
        self.assertEqual(
            (
                (FOURTH_REVISION, tuple(sorted(_FOURTH_TABLES))),
                (FIFTH_REVISION, tuple(sorted(_FIFTH_TABLES))),
                (FOURTH_REVISION, tuple(sorted(_FOURTH_TABLES))),
            ),
            (before, upgraded, downgraded),
        )

    async def test_live_run_scenario_identity_matches_its_mission_while_replay_stays_missionless(
        self,
    ) -> None:
        # Arrange
        await _apply(self.engine, HEAD_REVISION)
        sessions = create_session_factory(self.engine)
        mission = DashboardMission(
            mission_id="mission-scenario-fk-0001",
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            lifecycle="PLANNED",
            predecessor_mission_id=None,
        )
        mismatched = DashboardRun(
            run_identity="run-scenario-fk-mismatch",
            mode=StoredRunMode.DEGRADED_LIVE,
            scenario_id="different-scenario",
            scenario_revision=1,
            mission_id=mission.mission_id,
            run_id="run-scenario-fk-mismatch",
            session_id=None,
            prepared_initial_state=b'{"canonicalizationVersion":1,"stateVersion":1}',
        )
        matching = replace(
            mismatched,
            run_identity="run-scenario-fk-match",
            scenario_id=mission.scenario_id,
            run_id="run-scenario-fk-match",
        )
        replay = DashboardRun(
            run_identity="session-scenario-fk-0001",
            mode=StoredRunMode.REPLAY,
            scenario_id=mission.scenario_id,
            scenario_revision=mission.scenario_revision,
            mission_id=None,
            run_id=None,
            session_id="session-scenario-fk-0001",
            prepared_initial_state=b'{"canonicalizationVersion":1,"stateVersion":1}',
        )
        async with transaction(sessions) as session:
            await create_mission(session, mission)

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with transaction(sessions) as session:
                await create_run(session, mismatched)
        async with transaction(sessions) as session:
            await create_run(session, matching)
            await create_run(session, replay)
        async with transaction(sessions) as session:
            stored_matching = await run_by_identity(session, matching.run_identity)
            stored_replay = await run_by_identity(session, replay.run_identity)

        # Assert
        self.assertIn("fk_dashboard_run_mission_scenario", str(refused.value.orig))
        self.assertEqual((matching, replay), (stored_matching, stored_replay))

    async def test_start_reset_and_pending_recovery_keep_exact_bytes_and_history(self) -> None:
        # Arrange
        store, scenario, coordinator = await _dashboard_runtime(self.engine)

        # Act
        started = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            DASHBOARD_START_KEY,
            DASHBOARD_START_DIGEST,
        )
        start_repeat = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            DASHBOARD_START_KEY,
            DASHBOARD_START_DIGEST,
        )
        predecessor = await store.current_run()
        if predecessor is None or predecessor.mission_id is None:
            self.fail("live start did not select its durable predecessor")
        pending_reset = MutationProposal(
            DASHBOARD_RESET_KEY,
            MutationKind.RESET,
            RunMode.DEGRADED_LIVE,
            DASHBOARD_RESET_DIGEST,
            "wilderness-missing-person",
            1,
            "mission-test-0002",
            "run-test-0002",
            None,
            predecessor.mission_id,
        )
        await store.claim_operation(pending_reset)
        await coordinator.reconcile_pending()
        reset_repeat = await coordinator.reset(DASHBOARD_RESET_KEY, DASHBOARD_RESET_DIGEST)
        reset_repeat_again = await coordinator.reset(DASHBOARD_RESET_KEY, DASHBOARD_RESET_DIGEST)
        successor = await store.current_run()
        if successor is None or successor.mission_id is None or successor.run_id is None:
            self.fail("pending reset did not select its durable successor")
        scenario.status_mission_id = successor.mission_id
        scenario.status_run_id = successor.run_id
        with (
            patch.object(
                scenario,
                "start",
                side_effect=ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE),
            ) as uncertain_start,
            pytest.raises(ApiError) as uncertain,
        ):
            await coordinator.start(
                "wilderness-missing-person",
                RunMode.DEGRADED_LIVE,
                1,
                DASHBOARD_RECOVERY_KEY,
                DASHBOARD_RECOVERY_DIGEST,
            )
        await coordinator.reconcile_pending()
        recovered = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            DASHBOARD_RECOVERY_KEY,
            DASHBOARD_RECOVERY_DIGEST,
        )
        final = _required_current(await store.current_run())

        # Assert
        self.assertEqual((started.status, started.body), (start_repeat.status, start_repeat.body))
        self.assertEqual(
            (reset_repeat.status, reset_repeat.body),
            (reset_repeat_again.status, reset_repeat_again.body),
        )
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, uncertain.value.code)
        uncertain_start.assert_awaited_once()
        self.assertEqual(started.status, recovered.status)
        self.assertEqual(predecessor.mission_id, pending_reset.predecessor_mission_id)
        self.assertEqual((successor.mission_id, successor.run_id), (final.mission_id, final.run_id))
        self.assertTrue(final.started)
        self.assertEqual((1, 1), (len(scenario.starts), len(scenario.cancels)))

    async def test_broker_deduplication_is_visible_through_the_snapshot_read_path(self) -> None:
        # Arrange
        store, _, coordinator = await _dashboard_runtime(self.engine)
        await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            DASHBOARD_START_KEY,
            DASHBOARD_START_DIGEST,
        )
        current = _required_current(await store.current_run())
        if current.mission_id is None:
            self.fail("live start did not select an operational mission")
        event = BrokerEvent(
            source="urn:aerial-rescue:recorder-probe",
            event_id="event-store-probe-0001",
            source_sequence=7,
            payload_digest=DASHBOARD_EVENT_DIGEST,
        )
        record = _record(current.mission_id, "MISSION_LIFECYCLE")

        # Act
        async with transaction(store.session_factory) as session:
            accepted = await append_broker_event(cast("EventSession", session), event, record)
        async with transaction(store.session_factory) as session:
            duplicate = await append_broker_event(cast("EventSession", session), event, record)
        basis = await store.capture_snapshot_basis()
        if basis is None:
            self.fail("snapshot did not capture the selected live run")
        page = await store.read_events(current, 0, basis.audit_watermark, 10)
        suffix = await store.read_events(current, basis.audit_watermark, None, 10)

        # Assert
        self.assertEqual(BrokerEventOutcome.ACCEPTED, accepted.outcome)
        self.assertEqual(BrokerEventOutcome.DUPLICATE, duplicate.outcome)
        self.assertEqual(
            (accepted.audit_mission_id, accepted.audit_ordinal),
            (duplicate.audit_mission_id, duplicate.audit_ordinal),
        )
        self.assertEqual(current.identity, basis.current_run.identity)
        self.assertEqual(accepted.audit_ordinal, basis.audit_watermark)
        self.assertEqual(
            ((accepted.audit_ordinal, record.kind, record.payload),),
            tuple((item.audit_ordinal, item.kind, item.payload) for item in page),
        )
        self.assertEqual((), suffix)


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

    async def test_the_cluster_reports_the_deadlock_interval_the_lock_wait_is_derived_from(
        self,
    ) -> None:
        # Arrange
        statements = SHOWN_DEADLOCK_DETECTION

        # Act
        shown = await _shown(self.engine, statements)

        # Assert
        self.assertEqual(EXPECTED_DEADLOCK_DETECTION, shown)

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


CANONICAL_INSTANT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
"""ADR-0027's exact millisecond spelling, which the store persists rather than re-encodes."""

APPROVAL_MISSION: Final = "m-approval-probe"
PROPOSAL: Final = "p-approval-probe"
OPERATOR: Final = "operator-approval-probe"
ISSUED_WALL: Final = "2026-08-24T12:00:00.000Z"
ISSUED_MONOTONIC_MILLISECONDS: Final = 100_000
TIME_TO_LIVE_MILLISECONDS: Final = 60_000

ACTION_PARAMETERS: Final = {
    "canonicalizationVersion": 1,
    "commandType": "escalate_rescue",
    "latitudeMicrodegrees": 47_000_000,
    "longitudeMicrodegrees": -122_000_000,
}
CANDIDATE: Final = Proposal(
    mission_id=APPROVAL_MISSION, proposal_id=PROPOSAL, parameters=ACTION_PARAMETERS
)
"""What the gateway is about to publish. Its digest is recomputed at consumption, never trusted."""

APPROVED: Final = StoredApproval(
    mission_id=APPROVAL_MISSION,
    proposal_id=PROPOSAL,
    state=ApprovalState.APPROVED,
    operator_identity=OPERATOR,
    issued_wall=ISSUED_WALL,
    issued_monotonic_milliseconds=ISSUED_MONOTONIC_MILLISECONDS,
    time_to_live_milliseconds=TIME_TO_LIVE_MILLISECONDS,
    proposal_digest=proposal_digest(CANDIDATE),
)

INSIDE_THE_WINDOW: Final = ClockReading(
    wall=datetime.strptime(ISSUED_WALL, CANONICAL_INSTANT).replace(tzinfo=UTC)
    + timedelta(seconds=1),
    monotonic=timedelta(milliseconds=ISSUED_MONOTONIC_MILLISECONDS) + timedelta(seconds=1),
)
"""Both clocks read one second after issue, so neither regression nor expiry is what refuses."""

COMMITTED: Final = "committed"
NOT_A_PROTOCOL_STATE: Final = "consumed"

APPROVAL_ROWS: Final = table(
    APPROVAL_TABLE,
    column("mission_id", String),
    column("proposal_id", String),
    column("state", String),
    column("operator_identity", String),
    column("issued_wall", String),
    column("issued_monotonic_milliseconds", BigInteger),
    column("time_to_live_milliseconds", BigInteger),
    column("proposal_digest", String),
)
PERSISTED_STATE: Final = select(APPROVAL_ROWS.c.state).where(
    APPROVAL_ROWS.c.proposal_id == PROPOSAL
)


def _domain(stored: StoredApproval) -> Approval:
    """Map the persisted record into the domain value, which is the command gateway's step.

    The store never does this: it holds the canonical instant as text and the monotonic reading
    as a duration, because those are the forms it accepted. Turning them back into clock values
    belongs to the caller that owns the canonical representation.
    """
    return Approval(
        state=stored.state,
        operator_identity=stored.operator_identity,
        issued=ClockReading(
            wall=datetime.strptime(stored.issued_wall, CANONICAL_INSTANT).replace(tzinfo=UTC),
            monotonic=timedelta(milliseconds=stored.issued_monotonic_milliseconds),
        ),
        time_to_live=timedelta(milliseconds=stored.time_to_live_milliseconds),
        mission_id=stored.mission_id,
        proposal_id=stored.proposal_id,
        proposal_digest=stored.proposal_digest,
    )


async def _record_approved(engine: AsyncEngine) -> None:
    """Write the operator's decision in its own committed transaction."""
    async with transaction(create_session_factory(engine)) as session:
        await record(session, APPROVED)


async def _consume(
    engine: AsyncEngine,
    *,
    hold: asyncio.Event | None = None,
    took: asyncio.Future[bool] | None = None,
) -> str:
    """Run the sequence `packages/store/AGENTS.md` fixes, and report what it ended as.

    Load under the row lock, let the caller read its clocks and invoke guarded domain
    consumption while that lock is held, then persist conditionally. A refusal from either side
    is returned by name, because which side refused is the whole question ADR-0091 answers.
    """
    try:
        async with transaction(create_session_factory(engine)) as session:
            loaded = await load_for_update(session, PROPOSAL)
            if took is not None and not took.done():
                took.set_result(True)
            executed = consume(_domain(loaded), CANDIDATE, INSIDE_THE_WINDOW)
            await persist_consumed(session, replace(loaded, state=executed.state))
            if hold is not None:
                await hold.wait()
    except ApprovalError as denied:
        return denied.refusal.name
    except StoredApprovalError as refused:
        return refused.refusal.name
    return COMMITTED


async def _abandon_a_consumption(engine: AsyncEngine) -> None:
    """Consume, then abandon the transaction, so the rollback is what ends it."""
    with contextlib.suppress(AbandonedError):
        async with transaction(create_session_factory(engine)) as session:
            loaded = await load_for_update(session, PROPOSAL)
            executed = consume(_domain(loaded), CANDIDATE, INSIDE_THE_WINDOW)
            await persist_consumed(session, replace(loaded, state=executed.state))
            raise AbandonedError


@dataclass(frozen=True)
class Consumption:
    """What two consumers of one approval did, and whether the second had to wait."""

    first: str
    second: str
    second_waited: bool


async def _two_consumers_of_one_approval(engine: AsyncEngine) -> Consumption:
    """Run the race ADR-0091 selects its mechanism for, and observe the wait itself.

    The first consumer takes the row and holds the transaction open. The second is then started
    and watched for a window far below the lock wait: if it is still unfinished when the window
    ends, the row lock is what is holding it. Only then is the first released, so the second's
    decision is made against a committed row rather than beside it.
    """
    took: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    release = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        first = group.create_task(_consume(engine, hold=release, took=took))
        await took
        second = group.create_task(_consume(engine))
        finished, _ = await asyncio.wait({second}, timeout=HELD_WINDOW_SECONDS)
        release.set()
    return Consumption(first=first.result(), second=second.result(), second_waited=not finished)


async def _persisted_state(engine: AsyncEngine) -> str | None:
    """Return the state the approval row holds, read by a session that did not write it."""
    async with engine.connect() as connection:
        state = await connection.scalar(PERSISTED_STATE)
    return None if state is None else str(state)


class ApprovalConsumptionLiveTests(unittest.IsolatedAsyncioTestCase):
    """Each case gets a migrated database of its own, with one approved proposal in it."""

    target: DatabaseSettings
    engine: AsyncEngine

    @override
    async def asyncSetUp(self) -> None:
        """Create this case's database, bring it to head, and record one operator approval."""
        self.target = probe_target()
        await _on_maintenance_database(self.target, CREATE)
        self.engine = create_engine(self.target, BOUNDS)
        await _apply(self.engine, HEAD_REVISION)
        await _record_approved(self.engine)

    @override
    async def asyncTearDown(self) -> None:
        """Close every connection, then drop the database this case created."""
        await self.engine.dispose()
        await _on_maintenance_database(self.target, DROP)

    async def test_two_consumers_of_one_approval_commit_once_and_deny_once(self) -> None:
        # Arrange
        before = await _persisted_state(self.engine)

        # Act
        consumption = await _two_consumers_of_one_approval(self.engine)

        # Assert
        self.assertEqual(
            (
                ApprovalState.APPROVED.value,
                COMMITTED,
                "ALREADY_CONSUMED",
                True,
                ApprovalState.EXECUTED.value,
            ),
            (
                before,
                consumption.first,
                consumption.second,
                consumption.second_waited,
                await _persisted_state(self.engine),
            ),
        )

    async def test_a_consumed_approval_is_denied_again_by_a_later_transaction(self) -> None:
        # Arrange
        first = await _consume(self.engine)

        # Act
        second = await _consume(self.engine)

        # Assert
        self.assertEqual(
            (COMMITTED, "ALREADY_CONSUMED", ApprovalState.EXECUTED.value),
            (first, second, await _persisted_state(self.engine)),
        )

    async def test_an_abandoned_consumption_leaves_the_approval_consumable(self) -> None:
        # Arrange
        await _abandon_a_consumption(self.engine)

        # Act
        after = await _consume(self.engine)

        # Assert
        self.assertEqual(
            (COMMITTED, ApprovalState.EXECUTED.value),
            (after, await _persisted_state(self.engine)),
        )

    async def test_a_state_outside_the_protocol_is_refused_by_the_database(self) -> None:
        # Arrange
        forbidden = (
            update(APPROVAL_ROWS)
            .where(APPROVAL_ROWS.c.proposal_id == PROPOSAL)
            .values(state=NOT_A_PROTOCOL_STATE)
        )

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(forbidden)

        # Assert
        self.assertIn("ck_approval_state_in_protocol", str(refused.value.orig))

    async def test_one_proposal_cannot_hold_two_approvals(self) -> None:
        # Arrange
        duplicate = insert(APPROVAL_ROWS).values(
            mission_id=APPROVAL_MISSION,
            proposal_id=PROPOSAL,
            state=ApprovalState.APPROVED.value,
            operator_identity=OPERATOR,
            issued_wall=ISSUED_WALL,
            issued_monotonic_milliseconds=ISSUED_MONOTONIC_MILLISECONDS,
            time_to_live_milliseconds=TIME_TO_LIVE_MILLISECONDS,
            proposal_digest=APPROVED.proposal_digest,
        )

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(duplicate)

        # Assert
        self.assertIn("pk_approval", str(refused.value.orig))


IDEMPOTENCY_KEY: Final = "k-idempotency-probe"
OTHER_KEY: Final = "k-idempotency-probe-other"
BODY_DIGEST: Final = "ab" * 32
OTHER_BODY_DIGEST: Final = "cd" * 32
CLAIMED_AT: Final = "2026-08-24T12:00:00.000Z"
PRIOR_RESULT: Final = b'{"dispatched":true}'
NOT_A_PROTOCOL_KIND: Final = "escalation"

COMMAND_CLAIM: Final = StoredClaim(
    idempotency_key=IDEMPOTENCY_KEY,
    kind=IdempotencyKind.COMMAND,
    body_digest=BODY_DIGEST,
    mission_id=APPROVAL_MISSION,
    claimed_at=CLAIMED_AT,
)

CLAIM_ROWS: Final = table(
    IDEMPOTENCY_CLAIM_TABLE,
    column("idempotency_key", String),
    column("kind", String),
    column("body_digest", String),
    column("mission_id", String),
    column("result", LargeBinary),
    column("claimed_at", String),
)


async def _claim_and_answer(
    engine: AsyncEngine,
    request: StoredClaim,
    *,
    hold: asyncio.Event | None = None,
    took: asyncio.Future[bool] | None = None,
) -> str:
    """Claim a key, record its result in the same transaction, and report what happened.

    Recording inside the claiming transaction is what closes ADR-0092's in-flight window for
    this probe. A real gateway records later, which is why ``RESULT_NOT_RECORDED`` exists.
    """
    try:
        async with transaction(create_session_factory(engine)) as session:
            outcome = await claim(session, request)
            if took is not None and not took.done():
                took.set_result(True)
            if outcome.decision is IdempotencyDecision.EXECUTE:
                await record_result(session, request.idempotency_key, PRIOR_RESULT)
            if hold is not None:
                await hold.wait()
    except StoredClaimError as refused:
        return refused.refusal.name
    return outcome.decision.name


async def _abandon_a_claim(engine: AsyncEngine, request: StoredClaim) -> None:
    """Claim and answer, then abandon the transaction, so the rollback is what ends it."""
    with contextlib.suppress(AbandonedError):
        async with transaction(create_session_factory(engine)) as session:
            await claim(session, request)
            await record_result(session, request.idempotency_key, PRIOR_RESULT)
            raise AbandonedError


@dataclass(frozen=True)
class Claiming:
    """What two claimants of one key did, and whether the second had to wait."""

    first: str
    second: str
    second_waited: bool


async def _two_claimants_of_one_key(engine: AsyncEngine) -> Claiming:
    """Run the race ADR-0092 measures, and observe the wait itself."""
    took: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    release = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        first = group.create_task(_claim_and_answer(engine, COMMAND_CLAIM, hold=release, took=took))
        await took
        second = group.create_task(_claim_and_answer(engine, COMMAND_CLAIM))
        finished, _ = await asyncio.wait({second}, timeout=HELD_WINDOW_SECONDS)
        release.set()
    return Claiming(first=first.result(), second=second.result(), second_waited=not finished)


class IdempotencyClaimLiveTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_two_claimants_of_one_key_execute_once_and_replay_once(self) -> None:
        # Arrange
        key = IDEMPOTENCY_KEY

        # Act
        claiming = await _two_claimants_of_one_key(self.engine)

        # Assert
        self.assertEqual(
            (
                IdempotencyDecision.EXECUTE.name,
                IdempotencyDecision.RETURN_PRIOR_RESULT.name,
                True,
                PRIOR_RESULT,
            ),
            (
                claiming.first,
                claiming.second,
                claiming.second_waited,
                await _stored_result(self.engine, key),
            ),
        )

    async def test_a_claim_abandoned_before_commit_leaves_the_key_claimable(self) -> None:
        # Arrange
        await _abandon_a_claim(self.engine, COMMAND_CLAIM)

        # Act
        after = await _claim_and_answer(self.engine, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (IdempotencyDecision.EXECUTE.name, PRIOR_RESULT),
            (after, await _stored_result(self.engine, IDEMPOTENCY_KEY)),
        )

    async def test_a_key_replayed_with_a_different_body_is_refused(self) -> None:
        # Arrange
        first = await _claim_and_answer(self.engine, COMMAND_CLAIM)

        # Act
        replayed = await _claim_and_answer(
            self.engine, replace(COMMAND_CLAIM, body_digest=OTHER_BODY_DIGEST)
        )

        # Assert
        self.assertEqual((IdempotencyDecision.EXECUTE.name, "BODY_MISMATCH"), (first, replayed))

    async def test_a_known_approval_consumption_is_denied_rather_than_replayed(self) -> None:
        # Arrange
        consumption = replace(COMMAND_CLAIM, kind=IdempotencyKind.APPROVAL_CONSUMPTION)
        first = await _claim_and_answer(self.engine, consumption)

        # Act
        repeat = await _claim_and_answer(self.engine, consumption)

        # Assert
        self.assertEqual(
            (IdempotencyDecision.EXECUTE.name, IdempotencyDecision.DENY.name), (first, repeat)
        )

    async def test_a_recorded_result_is_never_overwritten(self) -> None:
        # Arrange
        await _claim_and_answer(self.engine, COMMAND_CLAIM)

        # Act
        with pytest.raises(StoredClaimError) as refused:
            async with transaction(create_session_factory(self.engine)) as session:
                await record_result(session, IDEMPOTENCY_KEY, b'{"dispatched":false}')

        # Assert
        self.assertEqual(
            ("RESULT_ALREADY_RECORDED", PRIOR_RESULT),
            (refused.value.refusal.name, await _stored_result(self.engine, IDEMPOTENCY_KEY)),
        )

    async def test_a_kind_outside_the_closed_set_is_refused_by_the_database(self) -> None:
        # Arrange
        forbidden = insert(CLAIM_ROWS).values(
            idempotency_key=OTHER_KEY,
            kind=NOT_A_PROTOCOL_KIND,
            body_digest=BODY_DIGEST,
            mission_id=APPROVAL_MISSION,
            claimed_at=CLAIMED_AT,
        )

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(forbidden)

        # Assert
        self.assertIn("ck_idempotency_claim_kind", str(refused.value.orig))


async def _stored_result(engine: AsyncEngine, idempotency_key: str) -> bytes | None:
    """Return the result stored for a key, read by a session that did not write it."""
    stored = select(CLAIM_ROWS.c.result).where(CLAIM_ROWS.c.idempotency_key == idempotency_key)
    async with engine.connect() as connection:
        result = await connection.scalar(stored)
    return None if result is None else bytes(result)


COMMAND: Final = "c-outbox-probe"
DRONE: Final = "drone-outbox-probe"
COMMAND_PAYLOAD: Final = b'{"commandType":"escalate_rescue"}'
STAGED_AT: Final = "2026-08-24T12:00:00.000Z"
NOT_A_PUBLICATION_STATE: Final = "published"

OUTBOX_ROWS: Final = table(
    COMMAND_OUTBOX_TABLE,
    column("command_id", String),
    column("mission_id", String),
    column("drone_id", String),
    column("payload", LargeBinary),
    column("state", String),
    column("correlation_id", String),
    column("causation_id", String),
    column("traceparent", String),
    column("staged_at", String),
)


def _command(command_id: str) -> StagedCommand:
    """Return one synthetic staged command for this probe."""
    return StagedCommand(
        command_id=command_id,
        mission_id=APPROVAL_MISSION,
        drone_id=DRONE,
        payload=COMMAND_PAYLOAD,
        correlation_id=CORRELATION,
        causation_id=None,
        traceparent=TRACEPARENT,
        staged_at=STAGED_AT,
    )


async def _fill_outbox(engine: AsyncEngine, records: int, state: str, prefix: str) -> None:
    """Put ``records`` rows in the outbox directly, which is faster than staging each one."""
    rows = [
        {
            "command_id": f"{prefix}-{ordinal:04d}",
            "mission_id": APPROVAL_MISSION,
            "drone_id": DRONE,
            "payload": COMMAND_PAYLOAD,
            "state": state,
            "correlation_id": CORRELATION,
            "causation_id": None,
            "traceparent": TRACEPARENT,
            "staged_at": STAGED_AT,
        }
        for ordinal in range(records)
    ]
    async with engine.begin() as connection:
        await connection.execute(insert(OUTBOX_ROWS), rows)


async def _stage_once(engine: AsyncEngine, command_id: str) -> str:
    """Stage one command in its own committed transaction and report what happened."""
    try:
        async with transaction(create_session_factory(engine)) as session:
            await stage(session, _command(command_id))
    except StagedCommandError as refused:
        return refused.refusal.name
    return STAGED_OUTCOME


async def _outbox_state(engine: AsyncEngine, command_id: str) -> str | None:
    """Return the state the outbox row holds, read by a session that did not write it."""
    stored = select(OUTBOX_ROWS.c.state).where(OUTBOX_ROWS.c.command_id == command_id)
    async with engine.connect() as connection:
        state = await connection.scalar(stored)
    return None if state is None else str(state)


STAGED_OUTCOME: Final = "staged"
OTHER_COMMAND: Final = "c-outbox-probe-other"


async def _confirm_one(engine: AsyncEngine, command_id: str) -> None:
    """Move one staged record to confirmed, so it stops counting against the bound."""
    async with transaction(create_session_factory(engine)) as session:
        await record_publication(session, command_id, OutboxState.STAGED, OutboxEvent.CONFIRM)


class CommandOutboxLiveTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_staged_command_is_visible_to_a_session_that_did_not_stage_it(self) -> None:
        # Arrange
        before = await _outbox_state(self.engine, COMMAND)

        # Act
        outcome = await _stage_once(self.engine, COMMAND)

        # Assert
        self.assertEqual(
            (None, STAGED_OUTCOME, OutboxState.STAGED.value),
            (before, outcome, await _outbox_state(self.engine, COMMAND)),
        )

    async def test_the_record_past_the_bound_is_refused_and_nothing_is_written(self) -> None:
        # Arrange
        await _fill_outbox(
            self.engine, MAXIMUM_UNCONFIRMED_RECORDS, OutboxState.STAGED.value, "c-staged"
        )

        # Act
        outcome = await _stage_once(self.engine, COMMAND)

        # Assert
        self.assertEqual(
            ("AT_CAPACITY", None), (outcome, await _outbox_state(self.engine, COMMAND))
        )

    async def test_a_confirmed_record_does_not_count_against_the_bound(self) -> None:
        # Arrange
        await _fill_outbox(
            self.engine, MAXIMUM_UNCONFIRMED_RECORDS, OutboxState.STAGED.value, "c-staged"
        )
        at_capacity = await _stage_once(self.engine, OTHER_COMMAND)
        await _confirm_one(self.engine, "c-staged-0000")

        # Act
        outcome = await _stage_once(self.engine, COMMAND)

        # Assert
        self.assertEqual(
            ("AT_CAPACITY", STAGED_OUTCOME, OutboxState.STAGED.value),
            (at_capacity, outcome, await _outbox_state(self.engine, COMMAND)),
        )

    async def test_a_publication_outcome_moves_the_record_along_one_edge(self) -> None:
        # Arrange
        await _stage_once(self.engine, COMMAND)

        # Act
        async with transaction(create_session_factory(self.engine)) as session:
            became = await record_publication(
                session, COMMAND, OutboxState.STAGED, OutboxEvent.AMBIGUOUS
            )

        # Assert
        self.assertEqual(
            (OutboxState.RECONCILIATION_NEEDED, OutboxState.RECONCILIATION_NEEDED.value),
            (became, await _outbox_state(self.engine, COMMAND)),
        )

    async def test_a_record_that_moved_on_refuses_an_outcome_computed_against_a_stale_state(
        self,
    ) -> None:
        # Arrange
        await _stage_once(self.engine, COMMAND)
        async with transaction(create_session_factory(self.engine)) as session:
            await record_publication(session, COMMAND, OutboxState.STAGED, OutboxEvent.CONFIRM)

        # Act
        with pytest.raises(StagedCommandError) as refused:
            async with transaction(create_session_factory(self.engine)) as session:
                await record_publication(
                    session, COMMAND, OutboxState.STAGED, OutboxEvent.AMBIGUOUS
                )

        # Assert
        self.assertEqual(
            ("NOT_IN_EXPECTED_STATE", OutboxState.CONFIRMED.value),
            (refused.value.refusal.name, await _outbox_state(self.engine, COMMAND)),
        )

    async def test_a_state_outside_the_lifecycle_is_refused_by_the_database(self) -> None:
        # Arrange
        await _stage_once(self.engine, COMMAND)
        forbidden = (
            update(OUTBOX_ROWS)
            .where(OUTBOX_ROWS.c.command_id == COMMAND)
            .values(state=NOT_A_PUBLICATION_STATE)
        )

        # Act
        with pytest.raises(IntegrityError) as refused:
            async with self.engine.begin() as connection:
                await connection.execute(forbidden)

        # Assert
        self.assertIn("ck_command_outbox_state", str(refused.value.orig))


@dataclass(frozen=True)
class DurableSet:
    """What the three tables hold, read together by a session that wrote none of them."""

    approval: str | None
    claim: bytes | None
    outbox: str | None


async def _durable_set(engine: AsyncEngine) -> DurableSet:
    """Return the three facts ADR-0006 requires to move together."""
    return DurableSet(
        approval=await _persisted_state(engine),
        claim=await _stored_result(engine, IDEMPOTENCY_KEY),
        outbox=await _outbox_state(engine, COMMAND),
    )


async def _the_atomic_set(engine: AsyncEngine, *, abandon: bool) -> None:
    """Run ADR-0006's three writes in one transaction, and optionally abandon it before commit.

    This is the sequence `packages/store/AGENTS.md` fixes, in its order: consume the approval
    under its row lock while the caller decides, claim the idempotency key, stage the exact
    command. A gateway would then publish after the commit; this probe stops at the commit,
    which is the boundary the atomicity claim is about.
    """
    with contextlib.suppress(AbandonedError):
        async with transaction(create_session_factory(engine)) as session:
            loaded = await load_for_update(session, PROPOSAL)
            executed = consume(_domain(loaded), CANDIDATE, INSIDE_THE_WINDOW)
            await persist_consumed(session, replace(loaded, state=executed.state))
            await claim(session, replace(COMMAND_CLAIM, kind=IdempotencyKind.APPROVAL_CONSUMPTION))
            await record_result(session, IDEMPOTENCY_KEY, PRIOR_RESULT)
            await stage(session, _command(COMMAND))
            if abandon:
                raise AbandonedError


class AtomicSetLiveTests(unittest.IsolatedAsyncioTestCase):
    """The three writes ADR-0006 requires to move together, against a real transaction."""

    target: DatabaseSettings
    engine: AsyncEngine

    @override
    async def asyncSetUp(self) -> None:
        """Create this case's database, bring it to head, and record one operator approval."""
        self.target = probe_target()
        await _on_maintenance_database(self.target, CREATE)
        self.engine = create_engine(self.target, BOUNDS)
        await _apply(self.engine, HEAD_REVISION)
        await _record_approved(self.engine)

    @override
    async def asyncTearDown(self) -> None:
        """Close every connection, then drop the database this case created."""
        await self.engine.dispose()
        await _on_maintenance_database(self.target, DROP)

    async def test_the_consumption_the_claim_and_the_staging_commit_together(self) -> None:
        # Arrange
        before = await _durable_set(self.engine)

        # Act
        await _the_atomic_set(self.engine, abandon=False)

        # Assert
        self.assertEqual(
            (
                DurableSet(approval=ApprovalState.APPROVED.value, claim=None, outbox=None),
                DurableSet(
                    approval=ApprovalState.EXECUTED.value,
                    claim=PRIOR_RESULT,
                    outbox=OutboxState.STAGED.value,
                ),
            ),
            (before, await _durable_set(self.engine)),
        )

    async def test_a_transaction_abandoned_after_all_three_writes_leaves_none_of_them(
        self,
    ) -> None:
        # Arrange
        before = await _durable_set(self.engine)

        # Act
        await _the_atomic_set(self.engine, abandon=True)

        # Assert
        self.assertEqual((before, before), (before, await _durable_set(self.engine)))

    async def test_the_approval_is_consumable_again_after_the_set_rolled_back(self) -> None:
        # Arrange
        await _the_atomic_set(self.engine, abandon=True)

        # Act
        await _the_atomic_set(self.engine, abandon=False)

        # Assert
        self.assertEqual(
            DurableSet(
                approval=ApprovalState.EXECUTED.value,
                claim=PRIOR_RESULT,
                outbox=OutboxState.STAGED.value,
            ),
            await _durable_set(self.engine),
        )


if __name__ == "__main__":
    unittest.main()
