"""Dashboard start/reset claims, pending recovery, and exact response replay.

ADR-0113 keeps these operations separate from command and approval idempotency. A conflicting
insert claims a lowercase UUIDv4 key without a read-then-write window; a repeat locks that row
before any current-pointer work. The stored operation owns the stable identities, so a retry
returns those rather than a newly proposed mission, run, or replay session.

Nothing here canonicalizes a request or response. The API supplies its already-computed request
digest and exact response bytes. Nothing opens a transaction either: the caller commits the
claim, prepared run, private handoff, and completion as separate recovery checkpoints so no
database lock is held across HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import Integer, LargeBinary, SmallInteger, String, column, select, table, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.migration import DASHBOARD_OPERATION_TABLE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

KEY_COLUMN: Final = "idempotency_key"
STATE_COLUMN: Final = "state"
ACCEPTED_OPERATION_STATUS: Final = 202

_KEY: Final = column(KEY_COLUMN, String)
_KIND: Final = column("operation_kind", String)
_MODE: Final = column("mode", String)
_DIGEST: Final = column("request_digest", String)
_SCENARIO: Final = column("scenario_id", String)
_SCENARIO_REVISION: Final = column("scenario_revision", Integer)
_MISSION: Final = column("mission_id", String)
_RUN: Final = column("run_id", String)
_SESSION: Final = column("session_id", String)
_PREDECESSOR: Final = column("predecessor_mission_id", String)
_STATE: Final = column(STATE_COLUMN, String)
_STATUS: Final = column("response_status", SmallInteger)
_BODY: Final = column("response_body", LargeBinary)

_OPERATION_ROWS: Final = table(
    DASHBOARD_OPERATION_TABLE,
    _KEY,
    _KIND,
    _MODE,
    _DIGEST,
    _SCENARIO,
    _SCENARIO_REVISION,
    _MISSION,
    _RUN,
    _SESSION,
    _PREDECESSOR,
    _STATE,
    _STATUS,
    _BODY,
)
_SELECTED_COLUMNS: Final = tuple(_OPERATION_ROWS.c)


class OperationKind(Enum):
    """The two dashboard mutations persisted in this table."""

    START = "start"
    RESET = "reset"


class OperationMode(Enum):
    """The accepted wire spellings for a live run or isolated replay session."""

    DEGRADED_LIVE = "degradedLive"
    REPLAY = "replay"


class OperationState(Enum):
    """Whether reconciliation is still required or exact bytes are available."""

    PENDING = "pending"
    COMPLETED = "completed"


class DashboardOperationRefusal(Enum):
    """Why a stored dashboard operation could not be used or changed."""

    CLAIM_VANISHED = "the conflicting operation key had no durable row to reconcile"
    ANOTHER_OPERATION_PENDING = "the one pending dashboard-operation slot is already occupied"
    UNKNOWN_KIND = "the stored dashboard operation kind is outside the closed set"
    UNKNOWN_MODE = "the stored dashboard operation mode is outside the closed set"
    UNKNOWN_STATE = "the stored dashboard operation state is outside the closed set"
    OPERATION_MISMATCH = "the key belongs to another dashboard operation"
    MODE_MISMATCH = "the key belongs to another dashboard mode"
    REQUEST_MISMATCH = "the key belongs to another canonical request body"
    SCENARIO_MISMATCH = "the key belongs to another scenario identity"
    UNREADABLE_IDENTITY = "the stored stable identities do not match the operation mode"
    UNREADABLE_CONTEXT = "the stored scenario or predecessor context is incompatible"
    UNREADABLE_RESULT = "the stored response is not exact status and bytes"
    COMPLETION_REJECTED = "the operation was absent or no longer pending"


class DashboardOperationError(StoreError):
    """A dashboard operation refused with structured, redacted context."""


@dataclass(frozen=True)
class OperationClaim:
    """The accepted values proposed for one new operation claim."""

    idempotency_key: str
    operation_kind: OperationKind
    mode: OperationMode
    request_digest: str
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    predecessor_mission_id: str | None


@dataclass(frozen=True)
class OperationResult:
    """The exact HTTP answer stored for every same-request repeat."""

    status: int
    body: bytes


@dataclass(frozen=True)
class DashboardOperation:
    """One durable operation, mapped without re-encoding any accepted value."""

    idempotency_key: str
    operation_kind: OperationKind
    mode: OperationMode
    request_digest: str
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    predecessor_mission_id: str | None
    state: OperationState
    result: OperationResult | None
    newly_claimed: bool

    @classmethod
    def from_claim(cls, claim: OperationClaim) -> DashboardOperation:
        """Return the pending representation written by a successful first claim."""
        return cls(
            idempotency_key=claim.idempotency_key,
            operation_kind=claim.operation_kind,
            mode=claim.mode,
            request_digest=claim.request_digest,
            scenario_id=claim.scenario_id,
            scenario_revision=claim.scenario_revision,
            mission_id=claim.mission_id,
            run_id=claim.run_id,
            session_id=claim.session_id,
            predecessor_mission_id=claim.predecessor_mission_id,
            state=OperationState.PENDING,
            result=None,
            newly_claimed=True,
        )


type OperationSelection = Select[tuple[object, ...]]
type AcceptedStartSelection = Select[tuple[str]]
type OperationRead = OperationSelection | AcceptedStartSelection


def claim_statement(request: OperationClaim) -> Insert:
    """Return the one conflicting insert that claims this dashboard key."""
    proposed = postgresql_insert(_OPERATION_ROWS).values(
        idempotency_key=request.idempotency_key,
        operation_kind=request.operation_kind.value,
        mode=request.mode.value,
        request_digest=request.request_digest,
        scenario_id=request.scenario_id,
        scenario_revision=request.scenario_revision,
        mission_id=request.mission_id,
        run_id=request.run_id,
        session_id=request.session_id,
        predecessor_mission_id=request.predecessor_mission_id,
        state=OperationState.PENDING.value,
    )
    return proposed.on_conflict_do_nothing().returning(_KEY)


def stored_statement(idempotency_key: str) -> OperationSelection:
    """Lock and read a repeat before a caller can lock the singleton pointer."""
    return (
        select(*_SELECTED_COLUMNS)
        .where(_OPERATION_ROWS.c[KEY_COLUMN] == idempotency_key)
        .with_for_update()
    )


def pending_statement() -> OperationSelection:
    """Lock the at-most-one pending operation for startup reconciliation."""
    return (
        select(*_SELECTED_COLUMNS)
        .where(_OPERATION_ROWS.c[STATE_COLUMN] == OperationState.PENDING.value)
        .limit(1)
        .with_for_update()
    )


def accepted_start_statement(run_id: str) -> AcceptedStartSelection:
    """Find the completed 202 start operation that makes one live run active."""
    return (
        select(_KEY)
        .where(_KIND.in_((OperationKind.START.value,)))
        .where(_MODE.in_((OperationMode.DEGRADED_LIVE.value,)))
        .where(_RUN.in_((run_id,)))
        .where(_STATE.in_((OperationState.COMPLETED.value,)))
        .where(_STATUS.in_((ACCEPTED_OPERATION_STATUS,)))
        .limit(1)
    )


def completion_statement(idempotency_key: str, result: OperationResult) -> Update:
    """Record exact response bytes once, guarded by the pending state in the statement."""
    return (
        update(_OPERATION_ROWS)
        .where(_OPERATION_ROWS.c[KEY_COLUMN] == idempotency_key)
        .where(_OPERATION_ROWS.c[STATE_COLUMN] == OperationState.PENDING.value)
        .values(
            state=OperationState.COMPLETED.value,
            response_status=result.status,
            response_body=result.body,
        )
        .returning(_KEY)
    )


class SelectedRows(Protocol):
    """The one-row result surface used by operation reads."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the selected row, or no row."""


class OperationSession(Protocol):
    """The caller-owned session operations needed by this repository."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return the single value produced by a claim or completion."""

    async def execute(self, statement: OperationRead, /) -> SelectedRows:
        """Run one locked operation read."""


async def claim(session: OperationSession, request: OperationClaim) -> DashboardOperation:
    """Claim a key or return the stable pending/completed operation it already owns."""
    claimed = await session.scalar(claim_statement(request))
    if claimed is not None:
        return DashboardOperation.from_claim(request)
    stored = await _load(session, stored_statement(request.idempotency_key))
    if stored is None:
        occupied = await _load(session, pending_statement())
        if occupied is not None:
            raise DashboardOperationError(
                DashboardOperationRefusal.ANOTHER_OPERATION_PENDING,
                occupied.idempotency_key,
            )
        raise DashboardOperationError(
            DashboardOperationRefusal.CLAIM_VANISHED, request.idempotency_key
        )
    _compare_request(request, stored)
    return stored


async def complete(
    session: OperationSession,
    idempotency_key: str,
    result: OperationResult,
) -> None:
    """Complete a pending operation without overwriting a prior response."""
    completed = await session.scalar(completion_statement(idempotency_key, result))
    if completed is None:
        raise DashboardOperationError(
            DashboardOperationRefusal.COMPLETION_REJECTED, idempotency_key
        )


async def pending(session: OperationSession) -> DashboardOperation | None:
    """Return the one pending operation startup must reconcile, when there is one."""
    return await _load(session, pending_statement())


async def accepted_start(session: OperationSession, run_id: str) -> bool:
    """Derive whether a live run was accepted without adding inert run state."""
    selected = await session.execute(accepted_start_statement(run_id))
    return selected.one_or_none() is not None


async def _load(session: OperationSession, statement: OperationRead) -> DashboardOperation | None:
    """Read and fail-closed map one stored operation."""
    selected = await session.execute(statement)
    row = selected.one_or_none()
    return None if row is None else _operation_from_row(row)


def _compare_request(request: OperationClaim, stored: DashboardOperation) -> None:
    """Refuse semantic conflicts before the canonical request digest comparison."""
    if stored.operation_kind is not request.operation_kind:
        raise DashboardOperationError(
            DashboardOperationRefusal.OPERATION_MISMATCH, request.operation_kind
        )
    if stored.mode is not request.mode:
        raise DashboardOperationError(DashboardOperationRefusal.MODE_MISMATCH, request.mode)
    if stored.request_digest != request.request_digest:
        raise DashboardOperationError(
            DashboardOperationRefusal.REQUEST_MISMATCH, request.request_digest
        )
    if (
        stored.scenario_id != request.scenario_id
        or stored.scenario_revision != request.scenario_revision
    ):
        raise DashboardOperationError(
            DashboardOperationRefusal.SCENARIO_MISMATCH,
            (request.scenario_id, request.scenario_revision),
        )


def _operation_from_row(row: Sequence[object]) -> DashboardOperation:
    """Map the fixed selected column order, refusing malformed durable representation."""
    if len(row) != len(_SELECTED_COLUMNS):
        raise DashboardOperationError(DashboardOperationRefusal.UNREADABLE_RESULT, len(row))
    (
        key,
        kind_value,
        mode_value,
        digest,
        scenario,
        revision,
        mission,
        run,
        replay,
        predecessor,
        state_value,
    ) = row[:11]
    status, body = row[11:]
    try:
        kind = OperationKind(kind_value)
    except (TypeError, ValueError) as unknown:
        raise DashboardOperationError(
            DashboardOperationRefusal.UNKNOWN_KIND, kind_value
        ) from unknown
    try:
        mode = OperationMode(mode_value)
    except (TypeError, ValueError) as unknown:
        raise DashboardOperationError(
            DashboardOperationRefusal.UNKNOWN_MODE, mode_value
        ) from unknown
    try:
        state = OperationState(state_value)
    except (TypeError, ValueError) as unknown:
        raise DashboardOperationError(
            DashboardOperationRefusal.UNKNOWN_STATE, state_value
        ) from unknown
    identity = _identity(mode, mission, run, replay)
    context = _context(kind, mode, scenario, revision, predecessor)
    result = _result(state, status, body)
    if not isinstance(key, str):
        raise DashboardOperationError(
            DashboardOperationRefusal.UNREADABLE_IDENTITY, type(key).__name__
        )
    if not isinstance(digest, str):
        raise DashboardOperationError(
            DashboardOperationRefusal.UNREADABLE_IDENTITY, type(digest).__name__
        )
    return DashboardOperation(
        idempotency_key=key,
        operation_kind=kind,
        mode=mode,
        request_digest=digest,
        scenario_id=context[0],
        scenario_revision=context[1],
        mission_id=identity[0],
        run_id=identity[1],
        session_id=identity[2],
        predecessor_mission_id=context[2],
        state=state,
        result=result,
        newly_claimed=False,
    )


def _context(
    kind: OperationKind,
    mode: OperationMode,
    scenario: object,
    revision: object,
    predecessor: object,
) -> tuple[str, int, str | None]:
    """Map the stored scenario and nullable history link without type coercion."""
    expected_null = kind is OperationKind.START or mode is OperationMode.REPLAY
    valid_predecessor = predecessor is None if expected_null else isinstance(predecessor, str)
    if (
        not isinstance(scenario, str)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not valid_predecessor
    ):
        raise DashboardOperationError(
            DashboardOperationRefusal.UNREADABLE_CONTEXT,
            (scenario, revision, predecessor),
        )
    return scenario, revision, predecessor if isinstance(predecessor, str) else None


def _identity(
    mode: OperationMode, mission: object, run: object, replay: object
) -> tuple[str | None, str | None, str | None]:
    """Map the mutually exclusive stable identity triple."""
    if (
        mode is OperationMode.DEGRADED_LIVE
        and isinstance(mission, str)
        and isinstance(run, str)
        and replay is None
    ):
        return mission, run, None
    if mode is OperationMode.REPLAY and mission is None and run is None and isinstance(replay, str):
        return None, None, replay
    raise DashboardOperationError(
        DashboardOperationRefusal.UNREADABLE_IDENTITY, (mission, run, replay)
    )


def _result(state: OperationState, status: object, body: object) -> OperationResult | None:
    """Map the nullable-until-completed exact response representation."""
    if state is OperationState.PENDING:
        if status is None and body is None:
            return None
        raise DashboardOperationError(DashboardOperationRefusal.UNREADABLE_RESULT, state)
    if isinstance(status, int) and not isinstance(status, bool) and isinstance(body, bytes):
        return OperationResult(status=status, body=body)
    raise DashboardOperationError(DashboardOperationRefusal.UNREADABLE_RESULT, state)
