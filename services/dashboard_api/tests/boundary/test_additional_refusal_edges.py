"""Additional fail-closed branches for dashboard HTTP and orchestration boundaries."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Final, cast
from unittest.mock import patch

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.boundary.documents import (
    CATALOG_SCHEMA,
    find_scenario,
    validated_document,
)
from aerial_rescue_dashboard_api.boundary.durable_application import (
    LiveGraphPorts,
    _activate_answer_mission,
    _depth,
    _lifespan,
    _mutation_input,
    _register_exception_handlers,
    _require_idempotency_key,
)
from aerial_rescue_dashboard_api.boundary.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.boundary.wire import _strict_literal_one
from aerial_rescue_dashboard_api.orchestration import OperationCoordinator
from aerial_rescue_dashboard_api.ports import (
    ClaimedOperation,
    CurrentRun,
    MutationKind,
    MutationProposal,
    ReplayPreparation,
    RunMode,
)
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.types import Message, Scope

from tests.dashboard_api_support import (
    FakeIdentifiers,
    FakeReplay,
    FakeScenario,
    FakeStore,
)

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
KEY_ONE: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
KEY_TWO: Final = "4984a66b-ff04-4128-94ea-24578dc54851"


def _fixture(name: str) -> bytes:
    """Return one current golden dashboard document."""
    return (REPOSITORY_ROOT / f"fixtures/golden/v1/dashboard/{name}/baseline.json").read_bytes()


def _coordinator(
    store: FakeStore,
    scenario: FakeScenario | None = None,
    replay: FakeReplay | None = None,
) -> OperationCoordinator:
    """Build one deterministic operation coordinator."""
    return OperationCoordinator(
        store,
        scenario or FakeScenario(_fixture("scenario-catalog")),
        replay or FakeReplay(_fixture("replay-bundle")),
        FakeIdentifiers(),
    )


def _operation() -> ClaimedOperation:
    """Return one pending durable operation representation."""
    proposal = MutationProposal(
        idempotency_key=KEY_ONE,
        kind=MutationKind.START,
        mode=RunMode.DEGRADED_LIVE,
        request_digest="aa" * 32,
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id="mission-test-0001",
        run_id="run-test-0001",
        session_id=None,
        predecessor_mission_id=None,
    )
    return ClaimedOperation.from_proposal(proposal)


def _scenario_document() -> Mapping[str, object]:
    """Return the validated wilderness scenario definition."""
    catalog = validated_document(
        CATALOG_SCHEMA,
        _fixture("scenario-catalog"),
        maximum_bytes=512 * 1024,
    )
    return find_scenario(catalog, "wilderness-missing-person", 1)


def _request(headers: list[tuple[bytes, bytes]], body: bytes = b"") -> Request:
    """Build one raw Starlette request with a single finite body message."""
    scope_value = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/scenarios/current/reset",
        "raw_path": b"/api/v1/scenarios/current/reset",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8080),
    }
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(cast(Scope, cast(object, scope_value)), receive)


async def _get(application: FastAPI, path: str) -> httpx.Response:
    """Call one in-process HTTP route without a live socket."""
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@dataclass
class _ClosingResource:
    """Record explicit application resource shutdown."""

    close_count: int = 0

    async def close(self) -> None:
        """Record one bounded close operation."""
        self.close_count += 1


@dataclass
class _Broker:
    """Record the durable broker lifecycle without opening a network connection."""

    calls: list[str]
    ready: bool = False

    async def startup(self) -> None:
        """Record one startup attempt."""
        self.calls.append("startup")
        self.ready = True

    async def activate_mission(self, mission_id: str) -> None:
        """Record the exact live mission selected for projection."""
        self.calls.append(f"activate:{mission_id}")

    async def shutdown(self) -> None:
        """Record reverse-order broker shutdown."""
        self.calls.append("shutdown")
        self.ready = False


@dataclass
class _Watch:
    """One lifecycle watch double that records its ordering against the broker's."""

    calls: list[str]

    async def start(self) -> None:
        """Record that observation began."""
        self.calls.append("watch-start")

    async def stop(self) -> None:
        """Record that observation ended."""
        self.calls.append("watch-stop")


class ApplicationRefusalEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_supports_resource_free_composition_and_closes_owned_resources(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        coordinator = _coordinator(store)
        resource = _ClosingResource()
        entered: list[str] = []

        # Act
        async with _lifespan(coordinator, None)(FastAPI()):
            entered.append("resource-free")
        async with _lifespan(coordinator, resource)(FastAPI()):
            entered.append("owned-resource")

        # Assert
        self.assertEqual(["resource-free", "owned-resource"], entered)
        self.assertEqual(1, resource.close_count)
        self.assertEqual(2, store.calls.count("pending"))

    async def test_broker_lifespan_requires_store_and_skips_activation_for_replay(self) -> None:
        # Arrange
        missing_store_broker = _Broker([])
        replay_store = FakeStore(
            current=CurrentRun(
                RunMode.REPLAY,
                "wilderness-missing-person",
                1,
                None,
                None,
                "session-test-0001",
            )
        )
        replay_broker = _Broker([])

        # Act
        with pytest.raises(RuntimeError, match="requires the durable dashboard store"):
            async with _lifespan(
                _coordinator(FakeStore()),
                None,
                LiveGraphPorts(broker=missing_store_broker),
            )(FastAPI()):
                self.fail("store-free broker lifespan must not yield")
        async with _lifespan(
            _coordinator(replay_store),
            None,
            LiveGraphPorts(broker=replay_broker, store=replay_store),
        )(FastAPI()):
            pass

        # Assert
        self.assertEqual(["shutdown"], missing_store_broker.calls)
        self.assertEqual(["startup", "shutdown"], replay_broker.calls)
        self.assertEqual(["pending", "current"], replay_store.calls)

    async def test_the_lifecycle_watch_starts_after_the_broker_and_stops_before_it(self) -> None:
        """Rows staged by the watch are published by the serving loop, so it must not outlive it."""
        # Arrange
        broker = _Broker([])
        store = FakeStore(
            current=CurrentRun(
                RunMode.DEGRADED_LIVE,
                "wilderness-missing-person",
                1,
                "mission-test-0001",
                "run-test-0001",
                None,
                started=True,
            )
        )
        watch = _Watch(broker.calls)

        # Act
        async with _lifespan(
            _coordinator(store),
            None,
            LiveGraphPorts(broker=broker, store=store, lifecycle_watch=watch),
        )(FastAPI()):
            pass

        # Assert
        self.assertEqual(
            ["startup", "activate:mission-test-0001", "watch-start", "watch-stop", "shutdown"],
            broker.calls,
        )

    async def test_a_composition_without_a_lifecycle_watch_still_completes(self) -> None:
        # Arrange
        broker = _Broker([])
        store = FakeStore()

        # Act
        async with _lifespan(_coordinator(store), None, LiveGraphPorts(broker=broker, store=store))(
            FastAPI()
        ):
            pass

        # Assert
        self.assertEqual(["startup", "shutdown"], broker.calls)

    async def test_answer_activation_accepts_only_a_live_response_with_a_mission(self) -> None:
        # Arrange
        broker = _Broker([])
        replay = canonical.canonical_bytes({"mode": "replay", "sessionId": "session-test-0001"})
        missing_mission = canonical.canonical_bytes({"mode": "degradedLive"})
        live = canonical.canonical_bytes({"missionId": "mission-test-0001", "mode": "degradedLive"})

        # Act
        await _activate_answer_mission(202, canonical.canonical_bytes([]), broker)
        await _activate_answer_mission(202, replay, broker)
        with pytest.raises(ApiError) as malformed_error:
            await _activate_answer_mission(202, missing_mission, broker)
        await _activate_answer_mission(202, live, broker)

        # Assert
        self.assertIs(ErrorCode.INTERNAL_FAILURE, malformed_error.value.code)
        self.assertEqual(["activate:mission-test-0001"], broker.calls)

    async def test_exception_handlers_map_validation_fallback_http_and_unexpected_failures(
        self,
    ) -> None:
        # Arrange
        application = FastAPI()
        _register_exception_handlers(application)
        unexpected_message = "must-not-cross-the-boundary"

        async def validation_failure() -> None:
            raise RequestValidationError([])

        async def unusual_http_failure() -> None:
            raise HTTPException(status_code=418)

        async def unexpected_failure() -> None:
            raise RuntimeError(unexpected_message)

        application.get("/validation")(validation_failure)
        application.get("/http")(unusual_http_failure)
        application.get("/unexpected")(unexpected_failure)

        # Act
        responses = (
            await _get(application, "/validation"),
            await _get(application, "/http"),
            await _get(application, "/unexpected"),
        )

        # Assert
        self.assertEqual((400, 500, 500), tuple(response.status_code for response in responses))
        self.assertEqual(
            ("SCHEMA_INVALID", "INTERNAL_FAILURE", "INTERNAL_FAILURE"),
            tuple(response.json()["errorCode"] for response in responses),
        )
        self.assertTrue(all("must-not-cross" not in response.text for response in responses))

    async def test_canonical_body_helpers_refuse_scalar_documents_non_ascii_keys_and_booleans(
        self,
    ) -> None:
        # Arrange
        scalar = _request(
            [
                (b"content-type", b"application/json"),
                (b"idempotency-key", KEY_ONE.encode()),
            ],
            b"1",
        )
        non_ascii_key = _request([(b"idempotency-key", b"\xff")])

        # Act
        with (
            patch("aerial_rescue_dashboard_api.boundary.application.parse_wire_document"),
            pytest.raises(ApiError) as scalar_error,
        ):
            await _mutation_input(scalar, "schema-test", "reset", None)
        with pytest.raises(ApiError) as key_error:
            _require_idempotency_key(non_ascii_key)
        with pytest.raises(ValueError, match="integer 1") as literal_error:
            _strict_literal_one(True)
        depth = _depth([{"leaf": 1}])

        # Assert
        self.assertIs(ErrorCode.SCHEMA_INVALID, scalar_error.value.code)
        self.assertIs(ErrorCode.IDEMPOTENCY_KEY_INVALID, key_error.value.code)
        self.assertEqual("Input should be the integer 1", str(literal_error.value))
        self.assertEqual(3, depth)


class OrchestrationRefusalEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_preparation_retains_only_validated_exact_bytes(self) -> None:
        # Arrange
        expected_fields = ("bundle_bytes",)

        # Act
        actual_fields = tuple(field.name for field in fields(ReplayPreparation))

        # Assert
        self.assertEqual(expected_fields, actual_fields)

    async def test_pending_catalog_refusal_completes_while_dependency_refusal_propagates(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        unknown = replace(_operation(), scenario_id="scenario-unknown")
        store.operations[unknown.idempotency_key] = unknown
        scenario_port = FakeScenario(_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario=scenario_port)
        direct = replace(_operation(), idempotency_key=KEY_TWO)

        # Act
        await coordinator.reconcile_pending()
        with (
            patch.object(
                coordinator,
                "_scenario_definition",
                side_effect=ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE),
            ),
            pytest.raises(ApiError) as dependency_error,
        ):
            await coordinator._scenario_or_refusal(direct)

        # Assert
        self.assertEqual("completed", store.operations[KEY_ONE].state.value)
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, dependency_error.value.code)
        self.assertEqual([], scenario_port.starts)

    async def test_live_start_and_cancellation_reject_conflicting_or_incomplete_run_identity(
        self,
    ) -> None:
        # Arrange
        selected = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "wilderness-missing-person",
            1,
            "mission-other",
            "run-other",
            None,
        )
        scenario_port = FakeScenario(_fixture("scenario-catalog"))
        coordinator = _coordinator(FakeStore(current=selected), scenario=scenario_port)
        operation = _operation()
        incomplete = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "wilderness-missing-person",
            1,
            "mission-test-0001",
            None,
            None,
        )
        scenario = _scenario_document()

        # Act
        with pytest.raises(ApiError) as selected_error:
            await coordinator._complete_live_start(operation, scenario, invoke_start=False)
        with pytest.raises(ApiError) as identity_error:
            await coordinator._cancel_predecessor(incomplete)

        # Assert
        self.assertIs(ErrorCode.RUN_CONFLICT, selected_error.value.code)
        self.assertIs(ErrorCode.RUN_CONFLICT, identity_error.value.code)
        self.assertEqual([], scenario_port.starts)
        self.assertEqual([], scenario_port.cancels)

    async def test_live_reset_refuses_catalog_identity_missing_successor_and_lost_predecessor(
        self,
    ) -> None:
        # Arrange
        unknown_predecessor = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "scenario-unknown",
            1,
            "mission-old",
            "run-old",
            None,
        )
        catalog_store = FakeStore(current=unknown_predecessor)
        catalog_coordinator = _coordinator(catalog_store)
        valid_predecessor = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "wilderness-missing-person",
            1,
            "mission-old",
            "run-old",
            None,
        )
        missing_successor_coordinator = _coordinator(FakeStore(current=valid_predecessor))
        missing_successor = replace(
            _operation(),
            kind=MutationKind.RESET,
            mission_id=None,
            run_id=None,
            predecessor_mission_id="mission-old",
        )
        lost_store = FakeStore(
            current=CurrentRun(
                RunMode.DEGRADED_LIVE,
                "wilderness-missing-person",
                1,
                "mission-current",
                "run-current",
                None,
            )
        )
        lost_coordinator = _coordinator(lost_store)
        lost_predecessor = replace(
            _operation(),
            kind=MutationKind.RESET,
            predecessor_mission_id="mission-missing",
        )

        # Act
        catalog_answer = await catalog_coordinator.reset(KEY_ONE, "aa" * 32)
        with pytest.raises(ApiError) as successor_error:
            await missing_successor_coordinator._complete_live_reset(missing_successor)
        with pytest.raises(ApiError) as predecessor_error:
            await lost_coordinator._reset_predecessor(lost_predecessor)

        # Assert
        self.assertEqual(404, catalog_answer.status)
        self.assertIn(b"SCENARIO_NOT_FOUND", catalog_answer.body)
        self.assertIs(ErrorCode.RUN_CONFLICT, successor_error.value.code)
        self.assertIs(ErrorCode.RUN_CONFLICT, predecessor_error.value.code)
        self.assertIs(unknown_predecessor, catalog_store.current)

    async def test_replay_refuses_bundle_scenario_and_revision_identity_mismatches(
        self,
    ) -> None:
        # Arrange
        operation = replace(
            _operation(),
            mode=RunMode.REPLAY,
            mission_id=None,
            run_id=None,
            session_id="session-test-0001",
        )
        scenario_store = FakeStore()
        revision_store = FakeStore()
        scenario_coordinator = _coordinator(scenario_store)
        revision_coordinator = _coordinator(revision_store)

        # Act
        with (
            patch(
                "aerial_rescue_dashboard_api.orchestration.validated_document",
                return_value={"scenarioId": "scenario-other", "scenarioRevision": 1},
            ),
            pytest.raises(ApiError) as scenario_error,
        ):
            await scenario_coordinator._complete_replay(operation)
        with (
            patch(
                "aerial_rescue_dashboard_api.orchestration.validated_document",
                return_value={
                    "scenarioId": "wilderness-missing-person",
                    "scenarioRevision": 2,
                },
            ),
            pytest.raises(ApiError) as revision_error,
        ):
            await revision_coordinator._complete_replay(operation)

        # Assert
        self.assertIs(ErrorCode.SCENARIO_NOT_FOUND, scenario_error.value.code)
        self.assertIs(ErrorCode.SCENARIO_REVISION_MISMATCH, revision_error.value.code)
        self.assertIsNone(scenario_store.current)
        self.assertIsNone(revision_store.current)
