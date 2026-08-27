"""Offline evidence for the live-run-to-mission scenario identity constraint."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from aerial_rescue_store.dashboard_runs import (
    DashboardRun,
    RunMode,
    recording_run_statement,
    run_statement,
)
from aerial_rescue_store.migration import migration_config, upgrade_statements
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

PROBE_URL: Final = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
FOURTH_TO_FIFTH: Final = "0004_command_outbox:0005_dashboard_runtime"
DIALECT: Final = create_engine(f"{DRIVER}://probe@127.0.0.1:5432/probe").dialect
PREPARED: Final = b'{"canonicalizationVersion":1,"stateVersion":1}'


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return bound values without interpolating them into rendered SQL."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _rendered(statement: ClauseElement) -> str:
    """Return SQL emitted for the package's PostgreSQL dialect."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


class DashboardScenarioIdentityMigrationTests(unittest.TestCase):
    def test_a_live_run_references_its_missions_complete_scenario_identity(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True, False),
            (
                "CONSTRAINT uq_dashboard_mission_scenario_identity "
                "UNIQUE (mission_id, scenario_id, scenario_revision)" in emitted,
                "CONSTRAINT fk_dashboard_run_mission_scenario "
                "FOREIGN KEY(mission_id, scenario_id, scenario_revision) "
                "REFERENCES dashboard_mission (mission_id, scenario_id, scenario_revision)"
                in emitted,
                "CONSTRAINT fk_dashboard_run_mission FOREIGN KEY(mission_id)" in emitted,
            ),
        )

    def test_the_run_insert_supplies_every_column_in_the_scenario_reference(self) -> None:
        # Arrange
        run = DashboardRun(
            run_identity="run-scenario-identity-0001",
            mode=RunMode.DEGRADED_LIVE,
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            mission_id="mission-scenario-identity-0001",
            run_id="run-scenario-identity-0001",
            session_id=None,
            prepared_initial_state=PREPARED,
        )

        # Act
        bound = _parameters(run_statement(run))

        # Assert
        self.assertEqual(
            (run.mission_id, run.scenario_id, run.scenario_revision),
            (bound["mission_id"], bound["scenario_id"], bound["scenario_revision"]),
        )

    def test_recording_reads_scenario_from_the_run_and_lifecycle_from_the_mission(self) -> None:
        # Arrange
        statement = recording_run_statement(
            "mission-scenario-identity-0001", "run-scenario-identity-0001"
        )

        # Act
        rendered = _rendered(statement)

        # Assert
        self.assertEqual(
            (True, True, True, False),
            (
                "dashboard_run.scenario_id" in rendered,
                "dashboard_run.scenario_revision" in rendered,
                "dashboard_mission.lifecycle" in rendered,
                "dashboard_mission.scenario_id" in rendered,
            ),
        )


if __name__ == "__main__":
    unittest.main()
