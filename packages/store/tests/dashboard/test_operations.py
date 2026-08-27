"""Durable dashboard mutation claims and exact response replay.

These tests compile the parameterized PostgreSQL statements and use a recording session to prove
call order and fail-closed row mapping. They do not claim unique-index or lock behavior; that needs
the disposable PostgreSQL class.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_store.dashboard.operations import (
    ACCEPTED_OPERATION_STATUS,
    DashboardOperation,
    DashboardOperationError,
    DashboardOperationRefusal,
    OperationClaim,
    OperationKind,
    OperationMode,
    OperationResult,
    OperationState,
    accepted_start,
    accepted_start_statement,
    claim,
    claim_statement,
    complete,
    completion_statement,
    pending,
    pending_statement,
    stored_statement,
)
from aerial_rescue_store.migration import DASHBOARD_OPERATION_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
KEY: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
MISSION: Final = "mission-store-0001"
RUN: Final = "run-store-0001"
SESSION: Final = "session-store-0001"
SCENARIO: Final = "wilderness-missing-person"
PREDECESSOR: Final = "mission-store-0000"
DIGEST: Final = "ab" * 32
OTHER_DIGEST: Final = "cd" * 32
RESPONSE: Final = b'{"operationVersion":"dashboard-start-response/v1"}'

LIVE_CLAIM: Final = OperationClaim(
    idempotency_key=KEY,
    operation_kind=OperationKind.START,
    mode=OperationMode.DEGRADED_LIVE,
    request_digest=DIGEST,
    scenario_id=SCENARIO,
    scenario_revision=1,
    mission_id=MISSION,
    run_id=RUN,
    session_id=None,
    predecessor_mission_id=None,
)
REPLAY_CLAIM: Final = OperationClaim(
    idempotency_key=KEY,
    operation_kind=OperationKind.RESET,
    mode=OperationMode.REPLAY,
    request_digest=DIGEST,
    scenario_id=SCENARIO,
    scenario_revision=1,
    mission_id=None,
    run_id=None,
    session_id=SESSION,
    predecessor_mission_id=None,
)
RESULT: Final = OperationResult(status=202, body=RESPONSE)


def _rendered(statement: ClauseElement) -> str:
    """Return the statement as the pinned PostgreSQL dialect would receive it."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values the statement binds rather than interpolates."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _stored_row(
    claim: OperationClaim = LIVE_CLAIM,
    *,
    state: str = "pending",
    status: object = None,
    body: object = None,
) -> tuple[object, ...]:
    """Return a row in the exact order the repository selects and maps it."""
    return (
        claim.idempotency_key,
        claim.operation_kind.value,
        claim.mode.value,
        claim.request_digest,
        claim.scenario_id,
        claim.scenario_revision,
        claim.mission_id,
        claim.run_id,
        claim.session_id,
        claim.predecessor_mission_id,
        state,
        status,
        body,
    )


@dataclass
class _Rows:
    """The one-row result surface the repository consumes."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the configured row."""
        return self.row


@dataclass
class _RecordingSession:
    """A deterministic session that records statements and returns canned values."""

    scalar_answer: object = KEY
    rows: list[Sequence[object] | None] = field(default_factory=list)
    scalars: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Record a scalar statement and return the configured answer."""
        self.scalars.append(_rendered(statement))
        return self.scalar_answer

    async def execute(self, statement: ClauseElement, /) -> _Rows:
        """Record a row statement and return its next configured row."""
        self.executed.append(_rendered(statement))
        return _Rows(self.rows.pop(0) if self.rows else None)


class OperationStatementTests(unittest.TestCase):
    def test_the_claim_uses_one_conflicting_insert_and_starts_pending(self) -> None:
        # Arrange
        request = LIVE_CLAIM

        # Act
        statement = claim_statement(request)
        rendered = _rendered(statement)
        bound = _parameters(statement)

        # Assert
        self.assertEqual(
            (
                True,
                OperationState.PENDING.value,
                MISSION,
                RUN,
                None,
                SCENARIO,
                1,
                None,
            ),
            (
                "ON CONFLICT DO NOTHING" in rendered,
                bound["state"],
                bound["mission_id"],
                bound["run_id"],
                bound.get("session_id"),
                bound["scenario_id"],
                bound["scenario_revision"],
                bound.get("predecessor_mission_id"),
            ),
        )

    def test_a_replay_claim_persists_only_its_session_identity(self) -> None:
        # Arrange
        request = REPLAY_CLAIM

        # Act
        bound = _parameters(claim_statement(request))

        # Assert
        self.assertEqual(
            (None, None, SESSION), (bound["mission_id"], bound["run_id"], bound["session_id"])
        )

    def test_a_repeat_locks_its_operation_before_any_pointer_can_be_locked(self) -> None:
        # Arrange
        key = KEY

        # Act
        rendered = _rendered(stored_statement(key))

        # Assert
        self.assertEqual(
            (True, True),
            (f"FROM {DASHBOARD_OPERATION_TABLE}" in rendered, rendered.endswith("FOR UPDATE")),
        )

    def test_completion_is_conditional_and_binds_exact_response_bytes_and_status(self) -> None:
        # Arrange
        result = RESULT

        # Act
        statement = completion_statement(KEY, result)
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                f"{DASHBOARD_OPERATION_TABLE}.state =" in rendered,
                result.status in bound,
                result.body in bound,
            ),
        )

    def test_startup_recovery_reads_only_the_single_pending_operation_under_lock(self) -> None:
        # Arrange
        no_input = None

        # Act
        rendered = _rendered(pending_statement())

        # Assert
        self.assertEqual(
            (None, True, True, True),
            (
                no_input,
                f"{DASHBOARD_OPERATION_TABLE}.state =" in rendered,
                "LIMIT" in rendered,
                rendered.endswith("FOR UPDATE"),
            ),
        )

    def test_started_state_is_derived_only_from_an_accepted_live_start_operation(self) -> None:
        # Arrange
        run_id = RUN

        # Act
        statement = accepted_start_statement(run_id)
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True, True, True),
            (
                f"FROM {DASHBOARD_OPERATION_TABLE}" in rendered,
                [OperationKind.START.value] in bound,
                [OperationMode.DEGRADED_LIVE.value] in bound,
                [run_id] in bound,
                [OperationState.COMPLETED.value] in bound,
                [ACCEPTED_OPERATION_STATUS] in bound,
            ),
        )


class OperationClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_first_claim_returns_the_stable_pending_identity_without_a_read(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        operation = await claim(session, LIVE_CLAIM)

        # Assert
        self.assertEqual(
            (LIVE_CLAIM.idempotency_key, RUN, OperationState.PENDING, True, 1, 0),
            (
                operation.idempotency_key,
                operation.run_id,
                operation.state,
                operation.newly_claimed,
                len(session.scalars),
                len(session.executed),
            ),
        )

    async def test_a_completed_repeat_returns_the_exact_stored_status_and_bytes(self) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answer=None,
            rows=[
                _stored_row(
                    state=OperationState.COMPLETED.value,
                    status=RESULT.status,
                    body=RESULT.body,
                )
            ],
        )

        # Act
        operation = await claim(session, replace(LIVE_CLAIM, run_id="unused-new-run"))

        # Assert
        self.assertEqual(
            (RUN, RESULT, False), (operation.run_id, operation.result, operation.newly_claimed)
        )

    async def test_a_same_key_with_another_operation_is_refused_before_digest_comparison(
        self,
    ) -> None:
        # Arrange
        request = replace(
            LIVE_CLAIM, operation_kind=OperationKind.RESET, request_digest=OTHER_DIGEST
        )
        session = _RecordingSession(scalar_answer=None, rows=[_stored_row()])

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, request)

        # Assert
        self.assertEqual(
            DashboardOperationRefusal.OPERATION_MISMATCH,
            refused.value.refusal,
        )

    async def test_a_same_key_and_operation_with_another_mode_is_refused(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None, rows=[_stored_row()])
        request = replace(REPLAY_CLAIM, operation_kind=OperationKind.START)

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, request)

        # Assert
        self.assertEqual(DashboardOperationRefusal.MODE_MISMATCH, refused.value.refusal)

    async def test_a_same_key_with_another_request_digest_is_refused_without_an_effect(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None, rows=[_stored_row()])

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, replace(LIVE_CLAIM, request_digest=OTHER_DIGEST))

        # Assert
        self.assertEqual(
            (DashboardOperationRefusal.REQUEST_MISMATCH, 1),
            (refused.value.refusal, len(session.scalars)),
        )

    async def test_a_conflict_without_a_stored_row_is_refused_as_a_vanished_claim(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None, rows=[None])

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, LIVE_CLAIM)

        # Assert
        self.assertEqual(DashboardOperationRefusal.CLAIM_VANISHED, refused.value.refusal)

    async def test_a_different_key_is_refused_when_the_single_pending_slot_is_occupied(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answer=None,
            rows=[None, _stored_row(claim=REPLAY_CLAIM)],
        )

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, LIVE_CLAIM)

        # Assert
        self.assertEqual(
            (DashboardOperationRefusal.ANOTHER_OPERATION_PENDING, KEY),
            (refused.value.refusal, refused.value.value),
        )

    async def test_malformed_completed_response_bytes_are_refused_not_coerced(self) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answer=None,
            rows=[
                _stored_row(
                    state=OperationState.COMPLETED.value,
                    status=202,
                    body="not bytes",
                )
            ],
        )

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, LIVE_CLAIM)

        # Assert
        self.assertEqual(DashboardOperationRefusal.UNREADABLE_RESULT, refused.value.refusal)

    async def test_every_incompatible_stored_operation_shape_is_refused_without_coercion(
        self,
    ) -> None:
        # Arrange
        rows: list[list[object]] = []
        short = list(_stored_row())[:-1]
        unknown_kind = list(_stored_row())
        unknown_kind[1] = "launch"
        unknown_mode = list(_stored_row())
        unknown_mode[2] = "live"
        unknown_state = list(_stored_row())
        unknown_state[10] = "running"
        bad_key = list(_stored_row())
        bad_key[0] = 7
        bad_digest = list(_stored_row())
        bad_digest[3] = 7
        bad_identity = list(_stored_row())
        bad_identity[6] = None
        bad_context = list(_stored_row())
        bad_context[4] = None
        dirty_pending = list(_stored_row(status=202))
        invalid_status = list(
            _stored_row(
                state=OperationState.COMPLETED.value,
                status=True,
                body=RESPONSE,
            )
        )
        rows.extend(
            (
                short,
                unknown_kind,
                unknown_mode,
                unknown_state,
                bad_key,
                bad_digest,
                bad_identity,
                bad_context,
                dirty_pending,
                invalid_status,
            )
        )
        refusals: list[DashboardOperationRefusal] = []

        # Act
        for row in rows:
            session = _RecordingSession(scalar_answer=None, rows=[row])
            try:
                await claim(session, LIVE_CLAIM)
            except DashboardOperationError as refused:
                refusals.append(cast("DashboardOperationRefusal", refused.refusal))

        # Assert
        self.assertEqual(
            (
                DashboardOperationRefusal.UNREADABLE_RESULT,
                DashboardOperationRefusal.UNKNOWN_KIND,
                DashboardOperationRefusal.UNKNOWN_MODE,
                DashboardOperationRefusal.UNKNOWN_STATE,
                DashboardOperationRefusal.UNREADABLE_IDENTITY,
                DashboardOperationRefusal.UNREADABLE_IDENTITY,
                DashboardOperationRefusal.UNREADABLE_IDENTITY,
                DashboardOperationRefusal.UNREADABLE_CONTEXT,
                DashboardOperationRefusal.UNREADABLE_RESULT,
                DashboardOperationRefusal.UNREADABLE_RESULT,
            ),
            tuple(refusals),
        )

    async def test_a_nonpositive_stored_scenario_revision_is_refused_without_coercion(self) -> None:
        # Arrange
        row = list(_stored_row())
        row[5] = 0
        session = _RecordingSession(scalar_answer=None, rows=[row])

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await claim(session, LIVE_CLAIM)

        # Assert
        self.assertEqual(DashboardOperationRefusal.UNREADABLE_CONTEXT, refused.value.refusal)


class OperationCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_records_the_response_once(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await complete(session, KEY, RESULT)

        # Assert
        self.assertEqual(
            (1, True),
            (
                len(session.scalars),
                RESPONSE in tuple(_parameters(completion_statement(KEY, RESULT)).values()),
            ),
        )

    async def test_a_nonpending_operation_is_never_overwritten(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None)

        # Act
        with pytest.raises(DashboardOperationError) as refused:
            await complete(session, KEY, RESULT)

        # Assert
        self.assertEqual(DashboardOperationRefusal.COMPLETION_REJECTED, refused.value.refusal)

    async def test_recovery_returns_the_only_pending_operation(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[_stored_row(claim=REPLAY_CLAIM)])

        # Act
        operation = await pending(session)

        # Assert
        self.assertIsInstance(operation, DashboardOperation)
        assert operation is not None
        self.assertEqual(
            (REPLAY_CLAIM.idempotency_key, SESSION, OperationState.PENDING),
            (operation.idempotency_key, operation.session_id, operation.state),
        )

    async def test_recovery_returns_none_when_no_operation_is_pending(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[None])

        # Act
        operation = await pending(session)

        # Assert
        self.assertIsNone(operation)

    async def test_run_started_requires_one_completed_202_start_row(self) -> None:
        # Arrange
        accepted = _RecordingSession(rows=[_stored_row()])
        absent = _RecordingSession(rows=[None])

        # Act
        is_started = await accepted_start(accepted, RUN)
        is_not_started = await accepted_start(absent, RUN)

        # Assert
        self.assertEqual((True, False), (is_started, is_not_started))


class OperationValueTests(unittest.TestCase):
    def test_a_pending_operation_has_no_result(self) -> None:
        # Arrange
        operation = DashboardOperation.from_claim(LIVE_CLAIM)

        # Act
        result = operation.result

        # Assert
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
