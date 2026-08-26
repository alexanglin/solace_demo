"""History-preserving dashboard mission, run, and singleton-pointer persistence."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_store.dashboard_runs import (
    CURRENT_POINTER_KEY,
    DashboardMission,
    DashboardRun,
    DashboardRunError,
    DashboardRunRefusal,
    RunMode,
    create_mission,
    create_run,
    current_run,
    current_run_for_share_statement,
    current_run_for_update_statement,
    mission_lifecycle_for_update,
    mission_lifecycle_for_update_statement,
    mission_statement,
    mission_transition_statement,
    move_current_run,
    pointer_transition_statement,
    run_by_identity,
    run_by_identity_statement,
    run_by_mission,
    run_by_mission_statement,
    run_statement,
    transition_mission,
)
from aerial_rescue_store.migration import (
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_RUN_TABLE,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
MISSION: Final = "mission-store-0001"
PREDECESSOR: Final = "mission-store-0000"
RUN: Final = "run-store-0001"
SESSION: Final = "session-store-0001"
SCENARIO: Final = "wilderness-search"
PREPARED: Final = b'{"canonicalizationVersion":1,"stateVersion":1}'

MISSION_RECORD: Final = DashboardMission(
    mission_id=MISSION,
    scenario_id=SCENARIO,
    scenario_revision=1,
    lifecycle="PLANNED",
    predecessor_mission_id=PREDECESSOR,
)
LIVE_RUN: Final = DashboardRun(
    run_identity=RUN,
    mode=RunMode.DEGRADED_LIVE,
    scenario_id=SCENARIO,
    scenario_revision=1,
    mission_id=MISSION,
    run_id=RUN,
    session_id=None,
    prepared_initial_state=PREPARED,
)
REPLAY_RUN: Final = DashboardRun(
    run_identity=SESSION,
    mode=RunMode.REPLAY,
    scenario_id=SCENARIO,
    scenario_revision=1,
    mission_id=None,
    run_id=None,
    session_id=SESSION,
    prepared_initial_state=PREPARED,
)


def _rendered(statement: ClauseElement) -> str:
    """Return SQL emitted for the package's PostgreSQL dialect."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return bound values without interpolating them into SQL."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _row(run: DashboardRun) -> tuple[object, ...]:
    """Return one run row in the repository's fixed selected order."""
    return (
        run.run_identity,
        run.mode.value,
        run.scenario_id,
        run.scenario_revision,
        run.mission_id,
        run.run_id,
        run.session_id,
        run.prepared_initial_state,
    )


@dataclass
class _Rows:
    """The one-row surface consumed by current-run reads."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the configured row."""
        return self.row


@dataclass
class _RecordingSession:
    """A fake caller-owned session with recorded parameterized statements."""

    scalar_answer: object = RUN
    rows: list[Sequence[object] | None] = field(default_factory=list)
    scalars: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Record a scalar write and return its canned result."""
        self.scalars.append(_rendered(statement))
        return self.scalar_answer

    async def execute(self, statement: ClauseElement, /) -> _Rows:
        """Record a current-run read and return its canned row."""
        self.executed.append(_rendered(statement))
        return _Rows(self.rows.pop(0) if self.rows else None)


class MissionAndRunStatementTests(unittest.TestCase):
    def test_a_successor_mission_keeps_its_predecessor_and_scenario_identity(self) -> None:
        # Arrange
        mission = MISSION_RECORD

        # Act
        bound = _parameters(mission_statement(mission))

        # Assert
        self.assertEqual(
            (PREDECESSOR, SCENARIO, 1),
            (bound["predecessor_mission_id"], bound["scenario_id"], bound["scenario_revision"]),
        )

    def test_a_run_keeps_the_exact_prepared_initial_state_bytes(self) -> None:
        # Arrange
        run = LIVE_RUN

        # Act
        bound = _parameters(run_statement(run))

        # Assert
        self.assertEqual(PREPARED, bound["prepared_initial_state"])

    def test_a_replay_run_keeps_a_session_without_an_operational_mission(self) -> None:
        # Arrange
        run = REPLAY_RUN

        # Act
        bound = _parameters(run_statement(run))

        # Assert
        self.assertEqual(
            (None, None, SESSION), (bound["mission_id"], bound["run_id"], bound["session_id"])
        )

    def test_mission_lifecycle_write_is_guarded_by_the_state_the_recorder_observed(self) -> None:
        # Arrange
        was = "PLANNED"

        # Act
        statement = mission_transition_statement(MISSION, was, "SEARCHING")
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, ("SEARCHING", MISSION, was)),
            (f"{DASHBOARD_MISSION_TABLE}.lifecycle =" in rendered, bound),
        )

    def test_recorder_locks_the_mission_lifecycle_before_deciding_a_transition(self) -> None:
        # Arrange
        mission_id = MISSION

        # Act
        statement = mission_lifecycle_for_update_statement(mission_id)
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, (mission_id,)),
            (f"FOR UPDATE OF {DASHBOARD_MISSION_TABLE}" in rendered, bound),
        )


class MissionLifecycleReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_locked_mission_lifecycle_read_is_strict_and_distinguishes_absence(
        self,
    ) -> None:
        # Arrange
        cases = (
            (_RecordingSession(rows=[("PLANNED",)]), "PLANNED", None),
            (
                _RecordingSession(rows=[None]),
                None,
                DashboardRunRefusal.UNKNOWN_MISSION,
            ),
            (
                _RecordingSession(rows=[(7,)]),
                None,
                DashboardRunRefusal.UNREADABLE_MISSION,
            ),
        )
        observed: list[str | None] = []
        refusals: list[DashboardRunRefusal | None] = []

        # Act
        for session, _expected, _expected_refusal in cases:
            try:
                observed.append(await mission_lifecycle_for_update(session, MISSION))
                refusals.append(None)
            except DashboardRunError as error:
                observed.append(None)
                refusals.append(cast("DashboardRunRefusal", error.refusal))

        # Assert
        self.assertEqual([expected for _session, expected, _refusal in cases], observed)
        self.assertEqual(
            [expected for _session, _value, expected in cases],
            refusals,
        )


class PointerStatementTests(unittest.TestCase):
    def test_historical_run_lookup_is_an_exact_bounded_primary_key_read(self) -> None:
        # Arrange
        identity = SESSION

        # Act
        rendered = _rendered(run_by_identity_statement(identity))
        bound = tuple(_parameters(run_by_identity_statement(identity)).values())

        # Assert
        self.assertEqual(
            (True, False, (identity,)),
            (f"FROM {DASHBOARD_RUN_TABLE}" in rendered, "FOR UPDATE" in rendered, bound),
        )

    def test_predecessor_lookup_uses_the_unique_mission_identity_without_a_lock(self) -> None:
        # Arrange
        mission_id = MISSION

        # Act
        rendered = _rendered(run_by_mission_statement(mission_id))
        bound = tuple(_parameters(run_by_mission_statement(mission_id)).values())

        # Assert
        self.assertEqual(
            (True, False, (mission_id,)),
            (f"FROM {DASHBOARD_RUN_TABLE}" in rendered, "FOR UPDATE" in rendered, bound),
        )

    def test_the_first_pointer_write_claims_only_the_singleton_row(self) -> None:
        # Arrange
        expected = None

        # Act
        statement = pointer_transition_statement(expected, RUN)
        rendered = _rendered(statement)
        bound = _parameters(statement)

        # Assert
        self.assertEqual(
            (True, CURRENT_POINTER_KEY, RUN),
            (
                "ON CONFLICT DO NOTHING" in rendered,
                bound["singleton_key"],
                bound["run_identity"],
            ),
        )

    def test_a_pointer_move_is_guarded_by_the_predecessor_run_identity(self) -> None:
        # Arrange
        predecessor_run = "run-store-0000"

        # Act
        statement = pointer_transition_statement(predecessor_run, RUN)
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, (RUN, CURRENT_POINTER_KEY, predecessor_run)),
            (f"{DASHBOARD_CURRENT_RUN_TABLE}.run_identity =" in rendered, bound),
        )

    def test_orchestration_locks_the_pointer_for_update(self) -> None:
        # Arrange
        no_input = None

        # Act
        rendered = _rendered(current_run_for_update_statement())

        # Assert
        self.assertEqual(
            (None, True),
            (no_input, f"FOR UPDATE OF {DASHBOARD_CURRENT_RUN_TABLE}" in rendered),
        )

    def test_snapshot_capture_locks_the_pointer_for_share(self) -> None:
        # Arrange
        no_input = None

        # Act
        rendered = _rendered(current_run_for_share_statement())

        # Assert
        self.assertEqual(
            (None, True),
            (no_input, f"FOR SHARE OF {DASHBOARD_CURRENT_RUN_TABLE}" in rendered),
        )


class RunRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_predecessor_maps_through_its_unique_mission_identity(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[_row(LIVE_RUN)])

        # Act
        run = await run_by_mission(session, MISSION)

        # Assert
        self.assertEqual(LIVE_RUN, run)

    async def test_historical_replay_session_maps_through_the_exact_run_identity(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[_row(REPLAY_RUN)])

        # Act
        run = await run_by_identity(session, SESSION)

        # Assert
        self.assertEqual(REPLAY_RUN, run)

    async def test_mission_and_run_creation_are_two_explicit_history_preserving_inserts(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await create_mission(session, MISSION_RECORD)
        await create_run(session, LIVE_RUN)

        # Assert
        self.assertEqual(
            (2, True, True),
            (
                len(session.scalars),
                session.scalars[0].startswith(f"INSERT INTO {DASHBOARD_MISSION_TABLE}"),
                session.scalars[1].startswith(f"INSERT INTO {DASHBOARD_RUN_TABLE}"),
            ),
        )

    async def test_a_rejected_history_insert_is_never_reported_as_created(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None)

        # Act
        with pytest.raises(DashboardRunError) as refused:
            await create_run(session, LIVE_RUN)

        # Assert
        self.assertEqual(DashboardRunRefusal.WRITE_REJECTED, refused.value.refusal)

    async def test_a_rejected_mission_insert_is_never_reported_as_created(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None)

        # Act
        with pytest.raises(DashboardRunError) as refused:
            await create_mission(session, MISSION_RECORD)

        # Assert
        self.assertEqual(DashboardRunRefusal.WRITE_REJECTED, refused.value.refusal)

    async def test_current_live_run_maps_exact_prepared_bytes(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[_row(LIVE_RUN)])

        # Act
        run = await current_run(session, shared=True)

        # Assert
        self.assertIsInstance(run, DashboardRun)
        assert run is not None
        self.assertEqual((RunMode.DEGRADED_LIVE, PREPARED), (run.mode, run.prepared_initial_state))

    async def test_current_replay_run_maps_its_session_identity(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[_row(REPLAY_RUN)])

        # Act
        run = await current_run(session, shared=False)

        # Assert
        self.assertIsInstance(run, DashboardRun)
        assert run is not None
        self.assertEqual((SESSION, None), (run.session_id, run.mission_id))

    async def test_no_current_pointer_is_represented_as_no_run(self) -> None:
        # Arrange
        session = _RecordingSession(rows=[None])

        # Act
        run = await current_run(session, shared=True)

        # Assert
        self.assertIsNone(run)

    async def test_a_pointer_that_moved_is_refused_instead_of_overwritten(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None)

        # Act
        with pytest.raises(DashboardRunError) as refused:
            await move_current_run(session, "run-store-0000", RUN)

        # Assert
        self.assertEqual(DashboardRunRefusal.POINTER_MOVED, refused.value.refusal)

    async def test_a_mission_that_moved_is_refused_instead_of_overwritten(self) -> None:
        # Arrange
        session = _RecordingSession(scalar_answer=None)

        # Act
        with pytest.raises(DashboardRunError) as refused:
            await transition_mission(session, MISSION, "PLANNED", "SEARCHING")

        # Assert
        self.assertEqual(DashboardRunRefusal.MISSION_MOVED, refused.value.refusal)

    async def test_guarded_pointer_and_mission_transitions_report_their_success(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await move_current_run(session, "run-store-0000", RUN)
        await transition_mission(session, MISSION, "PLANNED", "SEARCHING")

        # Assert
        self.assertEqual(
            (2, True), (len(session.scalars), all(" RETURNING " in sql for sql in session.scalars))
        )

    async def test_malformed_prepared_state_is_refused_instead_of_coerced(self) -> None:
        # Arrange
        malformed = (*_row(LIVE_RUN)[:7], "not bytes")
        session = _RecordingSession(rows=[malformed])

        # Act
        with pytest.raises(DashboardRunError) as refused:
            await current_run(session, shared=True)

        # Assert
        self.assertEqual(DashboardRunRefusal.UNREADABLE_RUN, refused.value.refusal)

    async def test_every_incompatible_run_shape_is_refused_without_coercion(self) -> None:
        # Arrange
        rows: list[list[object]] = []
        short = list(_row(LIVE_RUN))[:-1]
        unknown_mode = list(_row(LIVE_RUN))
        unknown_mode[1] = "live"
        bad_scenario = list(_row(LIVE_RUN))
        bad_scenario[2] = 7
        boolean_revision = list(_row(LIVE_RUN))
        boolean_revision[3] = True
        mismatched_live_identity = list(_row(LIVE_RUN))
        mismatched_live_identity[5] = "run-store-other"
        malformed_replay = list(_row(REPLAY_RUN))
        malformed_replay[4] = MISSION
        rows.extend(
            (
                short,
                unknown_mode,
                bad_scenario,
                boolean_revision,
                mismatched_live_identity,
                malformed_replay,
            )
        )
        refusals: list[DashboardRunRefusal] = []

        # Act
        for row in rows:
            session = _RecordingSession(rows=[row])
            try:
                await current_run(session, shared=True)
            except DashboardRunError as refused:
                refusals.append(cast("DashboardRunRefusal", refused.refusal))

        # Assert
        self.assertEqual(
            (
                DashboardRunRefusal.UNREADABLE_RUN,
                DashboardRunRefusal.UNKNOWN_MODE,
                DashboardRunRefusal.UNREADABLE_RUN,
                DashboardRunRefusal.UNREADABLE_RUN,
                DashboardRunRefusal.UNREADABLE_RUN,
                DashboardRunRefusal.UNREADABLE_RUN,
            ),
            tuple(refusals),
        )


if __name__ == "__main__":
    unittest.main()
