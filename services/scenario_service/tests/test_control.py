from __future__ import annotations

import json
import unittest
from typing import Final, cast

import pytest
from aerial_rescue_scenario_service.control import (
    FleetControlError,
    FleetControlRefusal,
    ScenarioCoordinator,
)
from aerial_rescue_scenario_service.http_runtime import ControlError, ControlRefusal
from aerial_rescue_scenario_service.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
    ScenarioCatalogResponse,
    ScenarioControlCancelRequest,
    ScenarioControlStartRequest,
    ScenarioDefinition,
)

pytestmark = [pytest.mark.unit]

MISSION_ID: Final = "mission-2026-0001"
RUN_ID: Final = "run-2026-0001"
SCENARIO_ID: Final = "wilderness-missing-person"


def _definition(*, simulated_count: int = 20, declared_count: int = 3) -> ScenarioDefinition:
    simulated = [
        {
            "identifier": f"drone-sim-{ordinal:02d}",
            "participation": "SIMULATED_DRONE",
            "sectorId": f"sector-{ordinal:02d}",
            "latitudeMicrodegrees": 44_472_000 + ordinal,
            "longitudeMicrodegrees": -79_248_000 + ordinal,
            "altitudeMetres": 120,
            "headingDegrees": 90,
            "groundSpeedCentimetresPerSecond": 850,
            "batteryPermille": 970,
            "northMicrodegreesPerTick": 0,
            "eastMicrodegreesPerTick": 76,
            "batteryDrainPermillePerTick": 2,
        }
        for ordinal in range(1, simulated_count + 1)
    ]
    roles = ("vision", "navigation", "communications")
    declared = [
        {
            "identifier": f"drone-{roles[index]}-{index + 1:02d}",
            "participation": "DECLARED_ONLY",
            "role": roles[index],
            "executionLabel": "DECLARED ONLY — NOT EXECUTED",
        }
        for index in range(declared_count)
    ]
    return ScenarioDefinition.model_validate(
        {
            "definitionVersion": 1,
            "identifier": SCENARIO_ID,
            "revision": 1,
            "title": "Synthetic wilderness search",
            "summary": "Explicit deterministic fleet inputs.",
            "searchAreaSquareMetres": 18_400_000,
            "lastKnownLocation": {
                "label": "North ridge trail",
                "latitudeMicrodegrees": 44_493_100,
                "longitudeMicrodegrees": -79_228_400,
            },
            "searchPolygon": {
                "vertices": [
                    {"latitudeMicrodegrees": 44_470_000, "longitudeMicrodegrees": -79_250_000},
                    {"latitudeMicrodegrees": 44_470_000, "longitudeMicrodegrees": -79_201_000},
                    {"latitudeMicrodegrees": 44_509_000, "longitudeMicrodegrees": -79_201_000},
                    {"latitudeMicrodegrees": 44_470_000, "longitudeMicrodegrees": -79_250_000},
                ]
            },
            "sectors": [
                {
                    "identifier": "sector-01",
                    "vertices": [
                        {
                            "latitudeMicrodegrees": 44_470_000,
                            "longitudeMicrodegrees": -79_250_000,
                        },
                        {
                            "latitudeMicrodegrees": 44_470_000,
                            "longitudeMicrodegrees": -79_241_000,
                        },
                        {
                            "latitudeMicrodegrees": 44_479_000,
                            "longitudeMicrodegrees": -79_241_000,
                        },
                        {
                            "latitudeMicrodegrees": 44_470_000,
                            "longitudeMicrodegrees": -79_250_000,
                        },
                    ],
                }
            ],
            "members": [*simulated, *declared],
            "tickIntervalMilliseconds": 1000,
            "connectivityThresholds": {
                "missesToDegraded": 3,
                "missesToOffline": 6,
                "heartbeatsToRecover": 2,
            },
            "ticksToSweep": 12,
            "absentHeartbeats": [{"droneId": "drone-sim-07", "tickOrdinal": 2}],
        }
    )


def _request(**changes: object) -> ScenarioControlStartRequest:
    document: dict[str, object] = {
        "controlVersion": 1,
        "scenarioId": SCENARIO_ID,
        "scenarioRevision": 1,
        "missionId": MISSION_ID,
        "runId": RUN_ID,
    }
    document.update(changes)
    return ScenarioControlStartRequest.model_validate(document)


def _fleet_status(
    state: str = "RUNNING", *, mission_id: str = MISSION_ID, run_id: str = RUN_ID
) -> FleetControlRunStatus:
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": mission_id,
            "runId": run_id,
            "state": state,
            "completedTickCount": 4,
            "telemetryPublicationCount": 80,
        }
    )


class FakeDefinitions:
    def __init__(self, definition: ScenarioDefinition) -> None:
        """Record one definition and begin outside a runtime epoch."""
        self.ready = False
        self.definition = definition
        self.calls: list[tuple[str, object]] = []

    async def startup(self) -> None:
        self.calls.append(("startup", None))
        self.ready = True

    async def shutdown(self) -> None:
        self.calls.append(("shutdown", None))
        self.ready = False

    async def load(self, scenario_id: str, revision: int) -> ScenarioDefinition:
        self.calls.append(("load", (scenario_id, revision)))
        return self.definition

    def catalog_response(self) -> ScenarioCatalogResponse:
        self.calls.append(("catalog_response", None))
        if not self.ready:
            raise ControlError(ControlRefusal.SCENARIO_NOT_FOUND)
        return ScenarioCatalogResponse.model_validate(
            {"catalogVersion": "scenario-catalog/v1", "scenarios": []}
        )


class FakeFleet:
    def __init__(self, status: FleetControlRunStatus | None = None) -> None:
        """Record an optional response and begin outside a runtime epoch."""
        self.ready = False
        self.response = status or _fleet_status()
        self.calls: list[tuple[str, object]] = []
        self.refusal: FleetControlRefusal | None = None
        self.startup_failure = False

    async def startup(self) -> None:
        self.calls.append(("startup", None))
        if self.startup_failure:
            message = "scripted startup failure"
            raise RuntimeError(message)
        self.ready = True

    async def shutdown(self) -> None:
        self.calls.append(("shutdown", None))
        self.ready = False

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        self.calls.append(("start", request))
        if self.refusal is not None:
            raise FleetControlError(self.refusal)
        return self.response

    async def status(self, run_id: str) -> FleetControlRunStatus:
        self.calls.append(("status", run_id))
        if self.refusal is not None:
            raise FleetControlError(self.refusal)
        return self.response

    async def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        self.calls.append(("cancel", (request, remaining_seconds)))
        if self.refusal is not None:
            raise FleetControlError(self.refusal)
        return self.response


class ScenarioCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_delegates_to_the_definition_source_only_when_ready(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)

        # Act
        with pytest.raises(ControlError) as unready:
            await coordinator.catalog()
        await coordinator.startup()
        response = await coordinator.catalog()

        # Assert
        self.assertEqual(ControlRefusal.SCENARIO_NOT_FOUND, unready.value.refusal)
        self.assertEqual("scenario-catalog/v1", response.catalog_version)
        self.assertEqual(
            [("catalog_response", None), ("startup", None), ("catalog_response", None)],
            definitions.calls,
        )

    async def test_start_projects_every_simulator_input_and_maps_fleet_status(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()

        # Act
        status = await coordinator.start(_request())

        # Assert
        self.assertEqual(status.state, "SEARCHING")
        self.assertEqual(
            {
                "controlVersion": 1,
                "scenarioId": "wilderness-missing-person",
                "scenarioRevision": 1,
                "missionId": "mission-2026-0001",
                "runId": "run-2026-0001",
                "state": "SEARCHING",
            },
            status.model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(definitions.calls[-1], ("load", (SCENARIO_ID, 1)))
        operation, value = fleet.calls[-1]
        fleet_request = cast("FleetControlStartRequest", value)
        self.assertEqual(operation, "start")
        self.assertIsInstance(fleet_request, FleetControlStartRequest)
        self.assertEqual(fleet_request.run_id, RUN_ID)
        self.assertEqual(fleet_request.scenario.mission_id, MISSION_ID)
        self.assertEqual(len(fleet_request.scenario.drones), 20)
        self.assertEqual(fleet_request.scenario.drones[6].drone_id, "drone-sim-07")
        self.assertEqual(fleet_request.scenario.absent_heartbeats[0].tick_ordinal, 2)

    async def test_same_run_and_body_queries_status_while_changed_body_conflicts(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()
        await coordinator.start(_request())

        # Act
        repeated = await coordinator.start(_request())
        with pytest.raises(ControlError) as captured:
            await coordinator.start(_request(missionId="another-mission"))

        # Assert
        self.assertEqual(repeated.state, "SEARCHING")
        self.assertEqual(captured.value.refusal, ControlRefusal.RUN_CONFLICT)
        self.assertEqual([name for name, _value in fleet.calls].count("start"), 1)
        self.assertEqual([name for name, _value in fleet.calls].count("status"), 1)

    async def test_status_and_cancel_preserve_run_and_mission_bindings(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet(_fleet_status("CANCELLED"))
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()
        fleet.response = _fleet_status("RUNNING")
        await coordinator.start(_request())
        fleet.response = _fleet_status("CANCELLED")
        exact_cancel = ScenarioControlCancelRequest.model_validate(
            {"controlVersion": 1, "missionId": MISSION_ID, "runId": RUN_ID}
        )
        wrong_mission = ScenarioControlCancelRequest.model_validate(
            {"controlVersion": 1, "missionId": "another-mission", "runId": RUN_ID}
        )

        # Act
        current = await coordinator.status(RUN_ID)
        cancelled = await coordinator.cancel(exact_cancel, 9.5)
        with pytest.raises(ControlError) as missing:
            await coordinator.status("missing-run")
        with pytest.raises(ControlError) as mismatch:
            await coordinator.cancel(wrong_mission, 9.5)

        # Assert
        self.assertEqual(current.state, "ABORTED")
        self.assertEqual(cancelled.state, "ABORTED")
        self.assertEqual(missing.value.refusal, ControlRefusal.RUN_NOT_FOUND)
        self.assertEqual(mismatch.value.refusal, ControlRefusal.PATH_BODY_MISMATCH)
        cancel_request, remaining = cast(
            "tuple[FleetControlCancelRequest, float]", fleet.calls[-1][1]
        )
        self.assertEqual(cancel_request.mission_id, MISSION_ID)
        self.assertEqual(remaining, 9.5)

    async def test_cancel_refuses_until_the_fleet_reports_a_terminal_state(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()
        await coordinator.start(_request())
        request = ScenarioControlCancelRequest.model_validate(
            {"controlVersion": 1, "missionId": MISSION_ID, "runId": RUN_ID}
        )

        # Act
        with pytest.raises(ControlError) as captured:
            await coordinator.cancel(request, 4)

        # Assert
        self.assertEqual(captured.value.refusal, ControlRefusal.CANCELLATION_NOT_ESTABLISHED)

    async def test_fleet_refusals_are_translated_without_exposing_fleet_details(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        fleet.refusal = FleetControlRefusal.RUN_CONFLICT
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()

        # Act
        with pytest.raises(ControlError) as conflict:
            await coordinator.start(_request())
        fleet.refusal = FleetControlRefusal.INTERNAL_FAILURE
        with pytest.raises(ControlError) as unavailable:
            await coordinator.start(_request(runId="another-run"))

        # Assert
        self.assertEqual(conflict.value.refusal, ControlRefusal.RUN_CONFLICT)
        self.assertEqual(unavailable.value.refusal, ControlRefusal.FLEET_UNAVAILABLE)
        self.assertNotIn("INTERNAL_FAILURE", unavailable.value.detail)

    async def test_prepared_workload_is_refused_before_the_fleet_call(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition(simulated_count=19))
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()

        # Act
        with pytest.raises(ControlError) as captured:
            await coordinator.start(_request())

        # Assert
        self.assertEqual(captured.value.refusal, ControlRefusal.SCENARIO_REVISION_MISMATCH)
        self.assertNotIn("start", [name for name, _value in fleet.calls])

    async def test_lifecycle_readiness_requires_both_owned_dependencies(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)

        # Act
        before = coordinator.ready
        await coordinator.startup()
        during = coordinator.ready
        fleet.ready = False
        degraded = coordinator.ready
        fleet.ready = True
        await coordinator.shutdown()
        after = coordinator.ready

        # Assert
        self.assertEqual((before, during, degraded, after), (False, True, False, False))
        self.assertEqual(definitions.calls[-1], ("shutdown", None))
        self.assertEqual(fleet.calls[-1], ("shutdown", None))
        self.assertEqual(json.dumps(coordinator.snapshot()), '{"runCount": 0, "started": false}')

    async def test_failed_fleet_startup_releases_the_definition_source(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        fleet.startup_failure = True
        coordinator = ScenarioCoordinator(definitions, fleet)

        # Act
        with pytest.raises(RuntimeError, match="scripted startup failure"):
            await coordinator.startup()

        # Assert
        self.assertFalse(coordinator.ready)
        self.assertEqual(
            definitions.calls,
            [("startup", None), ("shutdown", None)],
        )

    async def test_nonpositive_cancel_and_private_fleet_refusals_stay_typed(self) -> None:
        # Arrange
        definitions = FakeDefinitions(_definition())
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()
        await coordinator.start(_request())
        cancel = ScenarioControlCancelRequest.model_validate(
            {"controlVersion": 1, "missionId": MISSION_ID, "runId": RUN_ID}
        )

        # Act
        with pytest.raises(ControlError) as expired:
            await coordinator.cancel(cancel, 0)
        fleet.refusal = FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED
        with pytest.raises(ControlError) as cancellation:
            await coordinator.cancel(cancel, 1)
        fleet.refusal = FleetControlRefusal.RUN_NOT_FOUND
        with pytest.raises(ControlError) as missing:
            await coordinator.status(RUN_ID)

        # Assert
        self.assertEqual(expired.value.refusal, ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
        self.assertEqual(cancellation.value.refusal, ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
        self.assertEqual(missing.value.refusal, ControlRefusal.RUN_NOT_FOUND)

    async def test_definition_and_fleet_response_identities_must_match_the_bound_run(self) -> None:
        # Arrange
        mismatched_definition = _definition().model_copy(update={"identifier": "another-scenario"})
        definitions = FakeDefinitions(mismatched_definition)
        fleet = FakeFleet()
        coordinator = ScenarioCoordinator(definitions, fleet)
        await coordinator.startup()

        # Act
        with pytest.raises(ControlError) as definition_refusal:
            await coordinator.start(_request())
        definitions.definition = _definition()
        await coordinator.start(_request())
        fleet.response = _fleet_status(mission_id="another-mission")
        with pytest.raises(ControlError) as response_refusal:
            await coordinator.status(RUN_ID)

        # Assert
        self.assertEqual(
            definition_refusal.value.refusal,
            ControlRefusal.SCENARIO_REVISION_MISMATCH,
        )
        self.assertEqual(response_refusal.value.refusal, ControlRefusal.FLEET_UNAVAILABLE)
