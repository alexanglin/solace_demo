"""Dashboard mission history, prepared runs, and the singleton current-run pointer.

ADR-0113 makes reset a pointer move, never a delete. The API locks the pointer before it
creates or transitions mission/run rows and uses guarded writes so ``READ COMMITTED`` cannot
silently overwrite a concurrent decision. Snapshot capture uses a shared pointer lock and reads
the same exact prepared-state bytes that were accepted when the run was created.

This module does not decide mission transitions. A caller supplies the state it observed and the
state its authoritative broker event reached; the repository only makes that representation
change conditional on the observed value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import (
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    bindparam,
    column,
    insert,
    select,
    table,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.migration import (
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_RUN_TABLE,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

CURRENT_POINTER_KEY: Final = 1

_MISSION_ID: Final = column("mission_id", String)
_MISSION_SCENARIO: Final = column("scenario_id", String)
_MISSION_REVISION: Final = column("scenario_revision", Integer)
_MISSION_LIFECYCLE: Final = column("lifecycle", String)
_PREDECESSOR: Final = column("predecessor_mission_id", String)
_MISSION_ROWS: Final = table(
    DASHBOARD_MISSION_TABLE,
    _MISSION_ID,
    _MISSION_SCENARIO,
    _MISSION_REVISION,
    _MISSION_LIFECYCLE,
    _PREDECESSOR,
)

_RUN_IDENTITY: Final = column("run_identity", String)
_RUN_MODE: Final = column("mode", String)
_RUN_SCENARIO: Final = column("scenario_id", String)
_RUN_REVISION: Final = column("scenario_revision", Integer)
_RUN_MISSION: Final = column("mission_id", String)
_LIVE_RUN: Final = column("run_id", String)
_REPLAY_SESSION: Final = column("session_id", String)
_PREPARED_STATE: Final = column("prepared_initial_state", LargeBinary)
_RUN_ROWS: Final = table(
    DASHBOARD_RUN_TABLE,
    _RUN_IDENTITY,
    _RUN_MODE,
    _RUN_SCENARIO,
    _RUN_REVISION,
    _RUN_MISSION,
    _LIVE_RUN,
    _REPLAY_SESSION,
    _PREPARED_STATE,
)
_RUN_COLUMNS: Final = tuple(_RUN_ROWS.c)
_RECORDING_COLUMNS: Final = (
    _RUN_MISSION,
    _LIVE_RUN,
    _RUN_SCENARIO,
    _RUN_REVISION,
    _MISSION_LIFECYCLE,
    _PREPARED_STATE,
)

_POINTER_KEY: Final = column("singleton_key", SmallInteger)
_POINTER_RUN: Final = column("run_identity", String)
_POINTER_ROWS: Final = table(DASHBOARD_CURRENT_RUN_TABLE, _POINTER_KEY, _POINTER_RUN)


class RunMode(Enum):
    """The two current-run representations accepted by the dashboard contract."""

    DEGRADED_LIVE = "degradedLive"
    REPLAY = "replay"


class DashboardRunRefusal(Enum):
    """Why a mission, run, or pointer representation could not be stored or read."""

    WRITE_REJECTED = "the mission or run identity was not inserted"
    POINTER_MOVED = "the singleton pointer no longer named the expected predecessor run"
    MISSION_MOVED = "the mission no longer had the lifecycle the recorder observed"
    UNKNOWN_MISSION = "the recorder named no durable operational mission"
    UNREADABLE_MISSION = "the stored mission lifecycle is not a string"
    UNKNOWN_MODE = "the stored run mode is outside the closed set"
    UNREADABLE_RUN = "the stored run does not have the exact representation selected by its mode"


class DashboardRunError(StoreError):
    """A dashboard run repository refusal with structured context."""


@dataclass(frozen=True)
class DashboardMission:
    """One operational mission and its immutable history link."""

    mission_id: str
    scenario_id: str
    scenario_revision: int
    lifecycle: str
    predecessor_mission_id: str | None


@dataclass(frozen=True)
class DashboardRun:
    """One live run or replay session with its canonical prepared starting state."""

    run_identity: str
    mode: RunMode
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    prepared_initial_state: bytes


@dataclass(frozen=True)
class DashboardRecordingRun:
    """One exact live run joined to the mission state required for bounded export."""

    mission_id: str
    run_id: str
    scenario_id: str
    scenario_revision: int
    lifecycle: str
    prepared_initial_state: bytes


type RunSelection = Select[tuple[object, ...]]
type MissionSelection = Select[tuple[str]]
type RecordingRunSelection = Select[tuple[str, str, str, int, str, bytes]]
type RunRead = RunSelection | MissionSelection | RecordingRunSelection
type PointerTransition = Insert | Update


def mission_statement(mission: DashboardMission) -> Insert:
    """Insert one mission without replacing any predecessor history."""
    return (
        insert(_MISSION_ROWS)
        .values(
            mission_id=mission.mission_id,
            scenario_id=mission.scenario_id,
            scenario_revision=mission.scenario_revision,
            lifecycle=mission.lifecycle,
            predecessor_mission_id=mission.predecessor_mission_id,
        )
        .returning(_MISSION_ID)
    )


def run_statement(run: DashboardRun) -> Insert:
    """Insert one immutable run and its exact prepared-state bytes."""
    return (
        insert(_RUN_ROWS)
        .values(
            run_identity=run.run_identity,
            mode=run.mode.value,
            scenario_id=run.scenario_id,
            scenario_revision=run.scenario_revision,
            mission_id=run.mission_id,
            run_id=run.run_id,
            session_id=run.session_id,
            prepared_initial_state=run.prepared_initial_state,
        )
        .returning(_RUN_IDENTITY)
    )


def mission_transition_statement(
    mission_id: str, expected_lifecycle: str, lifecycle: str
) -> Update:
    """Persist a recorder-observed lifecycle only if the prior representation still matches."""
    return (
        update(_MISSION_ROWS)
        .where(_MISSION_ROWS.c["mission_id"] == mission_id)
        .where(_MISSION_ROWS.c["lifecycle"] == expected_lifecycle)
        .values(lifecycle=lifecycle)
        .returning(_MISSION_ID)
    )


def mission_lifecycle_for_update_statement(mission_id: str) -> MissionSelection:
    """Lock and select the authoritative mission lifecycle for recorder transition policy."""
    return (
        select(_MISSION_LIFECYCLE)
        .where(bindparam("mission_id", mission_id) == _MISSION_ID)
        .with_for_update(of=_MISSION_ROWS)
    )


def pointer_transition_statement(
    expected_run_identity: str | None, run_identity: str
) -> PointerTransition:
    """Claim the empty singleton pointer or move it from the exact expected predecessor."""
    if expected_run_identity is None:
        proposed = postgresql_insert(_POINTER_ROWS).values(
            singleton_key=CURRENT_POINTER_KEY, run_identity=run_identity
        )
        return proposed.on_conflict_do_nothing().returning(_POINTER_RUN)
    return (
        update(_POINTER_ROWS)
        .where(_POINTER_KEY == CURRENT_POINTER_KEY)
        .where(_POINTER_ROWS.c["run_identity"] == expected_run_identity)
        .values(run_identity=run_identity)
        .returning(_POINTER_RUN)
    )


def current_run_for_update_statement() -> RunSelection:
    """Lock the singleton pointer exclusively for start/reset orchestration."""
    return _current_run_statement(shared=False)


def current_run_for_share_statement() -> RunSelection:
    """Lock the singleton pointer for share while a snapshot captures its watermark."""
    return _current_run_statement(shared=True)


def run_by_identity_statement(run_identity: str) -> RunSelection:
    """Select one immutable historical run by its primary identity without a row lock."""
    return select(*_RUN_COLUMNS).where(_RUN_ROWS.c["run_identity"] == run_identity)


def run_by_mission_statement(mission_id: str) -> RunSelection:
    """Select the unique retained live run for one operational mission."""
    return select(*_RUN_COLUMNS).where(_RUN_ROWS.c["mission_id"] == mission_id)


def recording_run_statement(mission_id: str, run_id: str) -> RecordingRunSelection:
    """Select one exact live run and its mission lifecycle without locking or scanning history."""
    joined = _RUN_ROWS.join(_MISSION_ROWS, _RUN_MISSION == _MISSION_ID)
    return (
        select(*_RECORDING_COLUMNS)
        .select_from(joined)
        .where(_RUN_ROWS.c["mission_id"] == mission_id)
        .where(_RUN_ROWS.c["run_id"] == run_id)
    )


def _current_run_statement(*, shared: bool) -> RunSelection:
    """Select the pointer's run under the requested row-lock strength."""
    statement = (
        select(*_RUN_COLUMNS)
        .select_from(_POINTER_ROWS.join(_RUN_ROWS, _POINTER_RUN == _RUN_IDENTITY))
        .where(_POINTER_KEY == CURRENT_POINTER_KEY)
    )
    return statement.with_for_update(read=shared, of=_POINTER_ROWS)


class SelectedRows(Protocol):
    """The one-row result surface used by current-run reads."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the selected row, or no row."""


class RunSession(Protocol):
    """The caller-owned session surface needed by this repository."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return the identity produced by a guarded write."""

    async def execute(self, statement: RunRead, /) -> SelectedRows:
        """Run one current-pointer join."""


async def create_mission(session: RunSession, mission: DashboardMission) -> None:
    """Persist a mission without deleting or rewriting predecessor history."""
    created = await session.scalar(mission_statement(mission))
    if created is None:
        raise DashboardRunError(DashboardRunRefusal.WRITE_REJECTED, mission.mission_id)


async def create_run(session: RunSession, run: DashboardRun) -> None:
    """Persist a live run or replay session and its prepared initial state."""
    created = await session.scalar(run_statement(run))
    if created is None:
        raise DashboardRunError(DashboardRunRefusal.WRITE_REJECTED, run.run_identity)


async def current_run(session: RunSession, *, shared: bool) -> DashboardRun | None:
    """Read the current run under a shared snapshot lock or exclusive operation lock."""
    statement = current_run_for_share_statement() if shared else current_run_for_update_statement()
    selected = await session.execute(statement)
    row = selected.one_or_none()
    return None if row is None else _run_from_row(row)


async def run_by_identity(session: RunSession, run_identity: str) -> DashboardRun | None:
    """Read one immutable historical live run or replay session."""
    selected = await session.execute(run_by_identity_statement(run_identity))
    row = selected.one_or_none()
    return None if row is None else _run_from_row(row)


async def run_by_mission(session: RunSession, mission_id: str) -> DashboardRun | None:
    """Read the unique historical live run for a predecessor mission."""
    selected = await session.execute(run_by_mission_statement(mission_id))
    row = selected.one_or_none()
    return None if row is None else _run_from_row(row)


async def recording_run(
    session: RunSession, mission_id: str, run_id: str
) -> DashboardRecordingRun | None:
    """Read one exact live run and its authoritative terminal-state candidate."""
    selected = await session.execute(recording_run_statement(mission_id, run_id))
    row = selected.one_or_none()
    return None if row is None else _recording_run_from_row(row)


async def move_current_run(
    session: RunSession, expected_run_identity: str | None, run_identity: str
) -> None:
    """Move the singleton pointer only from the identity the caller locked and observed."""
    moved = await session.scalar(pointer_transition_statement(expected_run_identity, run_identity))
    if moved is None:
        raise DashboardRunError(DashboardRunRefusal.POINTER_MOVED, expected_run_identity)


async def transition_mission(
    session: RunSession,
    mission_id: str,
    expected_lifecycle: str,
    lifecycle: str,
) -> None:
    """Persist a caller-decided lifecycle without owning its transition policy."""
    moved = await session.scalar(
        mission_transition_statement(mission_id, expected_lifecycle, lifecycle)
    )
    if moved is None:
        raise DashboardRunError(DashboardRunRefusal.MISSION_MOVED, mission_id)


async def mission_lifecycle_for_update(session: RunSession, mission_id: str) -> str:
    """Read one mission lifecycle under an exclusive row lock."""
    selected = await session.execute(mission_lifecycle_for_update_statement(mission_id))
    row = selected.one_or_none()
    if row is None:
        raise DashboardRunError(DashboardRunRefusal.UNKNOWN_MISSION, mission_id)
    if len(row) != 1 or not isinstance(row[0], str):
        raise DashboardRunError(DashboardRunRefusal.UNREADABLE_MISSION, mission_id)
    return row[0]


def _run_from_row(row: Sequence[object]) -> DashboardRun:
    """Map one fixed run row and fail closed on incompatible stored values."""
    if len(row) != len(_RUN_COLUMNS):
        raise DashboardRunError(DashboardRunRefusal.UNREADABLE_RUN, len(row))
    identity, mode_value, scenario, revision, mission, live, replay, prepared = row
    try:
        mode = RunMode(mode_value)
    except (TypeError, ValueError) as unknown:
        raise DashboardRunError(DashboardRunRefusal.UNKNOWN_MODE, mode_value) from unknown
    if not (
        isinstance(identity, str)
        and isinstance(scenario, str)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and isinstance(prepared, bytes)
    ):
        raise DashboardRunError(DashboardRunRefusal.UNREADABLE_RUN, identity)
    values = _run_identity(mode, identity, mission, live, replay)
    return DashboardRun(
        run_identity=identity,
        mode=mode,
        scenario_id=scenario,
        scenario_revision=revision,
        mission_id=values[0],
        run_id=values[1],
        session_id=values[2],
        prepared_initial_state=prepared,
    )


def _recording_run_from_row(row: Sequence[object]) -> DashboardRecordingRun:
    """Map the purpose-specific export row without accepting driver-coerced values."""
    if len(row) != len(_RECORDING_COLUMNS):
        raise DashboardRunError(DashboardRunRefusal.UNREADABLE_RUN, len(row))
    mission, run, scenario, revision, lifecycle, prepared = row
    if not (
        isinstance(mission, str)
        and isinstance(run, str)
        and isinstance(scenario, str)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and isinstance(lifecycle, str)
        and isinstance(prepared, bytes)
    ):
        raise DashboardRunError(DashboardRunRefusal.UNREADABLE_RUN, run)
    return DashboardRecordingRun(mission, run, scenario, revision, lifecycle, prepared)


def _run_identity(
    mode: RunMode, identity: str, mission: object, live: object, replay: object
) -> tuple[str | None, str | None, str | None]:
    """Validate the mutually exclusive live and replay identity representations."""
    if (
        mode is RunMode.DEGRADED_LIVE
        and isinstance(mission, str)
        and isinstance(live, str)
        and replay is None
        and identity == live
    ):
        return mission, live, None
    if (
        mode is RunMode.REPLAY
        and mission is None
        and live is None
        and isinstance(replay, str)
        and identity == replay
    ):
        return None, None, replay
    raise DashboardRunError(DashboardRunRefusal.UNREADABLE_RUN, (identity, mission, live, replay))
