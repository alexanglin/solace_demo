"""FastAPI composition, raw HTTP boundary, route, and response tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from http import HTTPStatus
from pathlib import Path
from typing import cast, override

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.boundary.application import (
    ApplicationSettings,
    AssetOutcome,
    DataFrame,
    EventStream,
    JsonOutcome,
    Keepalive,
    create_live_application,
    create_replay_application,
)
from aerial_rescue_dashboard_api.boundary.http_contract import ROUTE_EXPECTATIONS
from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation
from aerial_rescue_dashboard_api.lifecycle import Dependency, RunMode, RuntimeReadiness
from aerial_rescue_dashboard_api.runtime_context import RuntimeContext
from fastapi import FastAPI
from fastapi.routing import APIRoute

from tests.http_support import asgi_client_for

_FIXTURES = Path(__file__).parents[4] / "fixtures" / "golden" / "v1" / "dashboard"
_KEY = "00000000-0000-4000-8000-000000000001"
_COMMAND_BODY = (
    b'{"action":{"commandType":"assign-sector","droneId":"drone-01",'
    b'"sectorId":"sector-01"},"missionId":"mission-synthetic-0001"}'
)
_START_LIVE_BODY = b'{"mode":"degradedLive","scenarioRevision":1}'
_START_REPLAY_BODY = b'{"mode":"replay","scenarioRevision":1}'
_client = asgi_client_for("http://localhost:8080")


def _schema(name: str) -> str:
    return f"https://aerial-rescue.invalid/schemas/v1/dashboard/{name}.schema.json"


def _fixture(name: str) -> bytes:
    raw = (_FIXTURES / name / "baseline.json").read_bytes()
    return canonical.canonical_bytes(canonical.decode(raw))


def _settings() -> ApplicationSettings:
    return ApplicationSettings(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        asset_entrypoint="index-deadbeef.js",
    )


def _context() -> RuntimeContext:
    return RuntimeContext(
        runtime_id="runtime-synthetic-0001",
        operator_id="local-operator",
        bearer="current-runtime-bearer",
    )


def _authorized_headers() -> dict[str, str]:
    return {
        "host": "localhost:8080",
        "origin": "http://localhost:8080",
        "authorization": "Bearer current-runtime-bearer",
        "content-type": "application/json",
        "idempotency-key": _KEY,
    }


class _FiniteEventStream(EventStream):
    def __init__(self) -> None:
        self.close_calls = 0
        self._frames = iter(
            (
                DataFrame("snapshot", _fixture("dashboard-snapshot")),
                Keepalive(),
                DataFrame("dashboard-event", _fixture("dashboard-event-frame")),
            )
        )

    @override
    def __aiter__(self) -> AsyncIterator[DataFrame | Keepalive]:
        return self

    async def __anext__(self) -> DataFrame | Keepalive:
        try:
            return next(self._frames)
        except StopIteration as error:
            raise StopAsyncIteration from error

    @override
    async def close(self) -> None:
        self.close_calls += 1


class _CommonOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.stream = _FiniteEventStream()

    async def open(self) -> None:
        self.calls.append(("open", None))

    async def close(self) -> None:
        self.calls.append(("close", None))

    async def scenarios(self) -> JsonOutcome:
        self.calls.append(("scenarios", None))
        return JsonOutcome(200, _schema("scenario-catalog"), _fixture("scenario-catalog"))

    async def open_events(self) -> EventStream:
        self.calls.append(("events", None))
        return self.stream

    async def replay_bundle(self, session_id: str) -> JsonOutcome:
        self.calls.append(("replay", session_id))
        return JsonOutcome(200, _schema("replay-bundle"), _fixture("replay-bundle"))

    async def asset(self, asset: str) -> AssetOutcome | None:
        self.calls.append(("asset", asset))
        if asset != "index-deadbeef.js":
            return None
        return AssetOutcome("application/javascript", b"export const ready = true;\n")


class _LiveOperations(_CommonOperations):
    async def start_scenario(self, scenario_id: str, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("start", (scenario_id, mutation)))
        return JsonOutcome(202, _schema("start-response"), _fixture("start-response"))

    async def reset(self, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("reset", mutation))
        return JsonOutcome(202, _schema("reset-response"), _fixture("reset-response"))

    async def command(self, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("command", mutation))
        return JsonOutcome(202, _schema("command-response"), _fixture("command-response"))

    async def decide_proposal(self, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("decision", mutation))
        return JsonOutcome(
            202,
            _schema("proposal-decision-response"),
            _fixture("proposal-decision-response"),
        )


class _ReplayOperations(_CommonOperations):
    async def start_replay(self, scenario_id: str, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("start-replay", (scenario_id, mutation)))
        body = canonical.canonical_bytes(
            {
                "declaredCount": 23,
                "declaredOnlyCount": 3,
                "mode": "replay",
                "operationVersion": "dashboard-start-response/v1",
                "sessionId": "replay-session-synthetic-0001",
                "simulatedCount": 20,
            }
        )
        return JsonOutcome(202, _schema("start-response"), body)

    async def reset_replay(self, mutation: AuthorizedMutation) -> JsonOutcome:
        self.calls.append(("reset-replay", mutation))
        body = canonical.canonical_bytes(
            {
                "declaredCount": 23,
                "declaredOnlyCount": 3,
                "mode": "replay",
                "operationVersion": "dashboard-reset-response/v1",
                "sessionId": "replay-session-synthetic-0002",
                "simulatedCount": 20,
            }
        )
        return JsonOutcome(202, _schema("reset-response"), body)


class _FailingOpenOperations(_LiveOperations):
    @override
    async def open(self) -> None:
        self.calls.append(("open", None))
        message = "synthetic open failure"
        raise RuntimeError(message)


class _ScenarioOutcomeOperations(_LiveOperations):
    def __init__(self, outcome: JsonOutcome) -> None:
        super().__init__()
        self._outcome = outcome

    @override
    async def scenarios(self) -> JsonOutcome:
        self.calls.append(("scenarios", None))
        return self._outcome


class _OverloadEventStream(EventStream):
    def __init__(self) -> None:
        self.close_calls = 0
        self._sent = False

    @override
    def __aiter__(self) -> AsyncIterator[DataFrame | Keepalive]:
        return self

    async def __anext__(self) -> DataFrame | Keepalive:
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return DataFrame("stream-overloaded", _fixture("stream-overloaded"))

    @override
    async def close(self) -> None:
        self.close_calls += 1


class _OverloadOperations(_LiveOperations):
    def __init__(self) -> None:
        super().__init__()
        self.overload_stream = _OverloadEventStream()

    @override
    async def open_events(self) -> EventStream:
        self.calls.append(("events", None))
        return self.overload_stream


def _ready(mode: RunMode) -> RuntimeReadiness:
    readiness = RuntimeReadiness(mode)
    dependencies = (
        (Dependency.REPLAY_INPUT,)
        if mode is RunMode.REPLAY
        else (
            Dependency.STORE,
            Dependency.SCENARIO_CONTROL,
            Dependency.BROKER_DELIVERY,
        )
    )
    for dependency in dependencies:
        readiness.set_dependency(dependency, ready=True)
    return readiness


def _live_application(
    operations: _LiveOperations,
    readiness: RuntimeReadiness | None = None,
) -> FastAPI:
    """Build the live application with the standard deterministic test boundary."""
    selected = _ready(RunMode.DEGRADED_LIVE) if readiness is None else readiness
    return create_live_application(_settings(), _context(), selected, operations)


def test_fastapi_route_inventory_is_exact_and_has_no_framework_admin_routes() -> None:
    # Arrange
    app = _live_application(_LiveOperations())
    expected = {(method, path) for method, path, *_rest in ROUTE_EXPECTATIONS}

    # Act
    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }

    # Assert
    assert actual == expected
    assert all(isinstance(route, APIRoute) for route in app.routes)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


@pytest.mark.asyncio
async def test_health_stays_live_while_mode_specific_readiness_tracks_dependency_loss() -> None:
    # Arrange
    operations = _LiveOperations()
    readiness = _ready(RunMode.DEGRADED_LIVE)
    app = _live_application(operations, readiness)

    # Act
    async with _client(app) as client:
        healthy = await client.get("/api/v1/health", headers={"host": "localhost:8080"})
        ready = await client.get(
            "/api/v1/readiness?mode=degradedLive",
            headers={"host": "localhost:8080"},
        )
        readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)
        degraded = await client.get(
            "/api/v1/readiness?mode=degradedLive",
            headers={"host": "localhost:8080"},
        )

    # Assert
    assert healthy.status_code == HTTPStatus.OK
    assert healthy.json() == {
        "healthVersion": "dashboard-health/v1",
        "status": "alive",
    }
    assert ready.json()["ready"] is True
    assert degraded.json() == {
        "mode": "degradedLive",
        "readinessVersion": "dashboard-readiness/v1",
        "ready": False,
        "reasons": ["broker-delivery-unavailable"],
    }
    assert operations.calls[0] == ("open", None)
    assert operations.calls[-1] == ("close", None)


@pytest.mark.asyncio
async def test_raw_host_boundary_refuses_before_mutation_body_or_operation_effect() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    headers = _authorized_headers() | {"host": "localhost.evil.invalid:8080"}

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=headers,
            content=b"not-even-json",
        )

    # Assert
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errorCode"] == "HOST_INVALID"
    assert not any(call[0] == "command" for call in operations.calls)
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_command_route_passes_only_authorized_canonical_input_to_typed_operation() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=_authorized_headers(),
            content=_COMMAND_BODY,
        )

    # Assert
    command_calls = [call for call in operations.calls if call[0] == "command"]
    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.content == _fixture("command-response")
    assert len(command_calls) == 1
    mutation = command_calls[0][1]
    assert isinstance(mutation, AuthorizedMutation)
    assert mutation.operator_id == "local-operator"
    assert mutation.ingress.canonical_body == _COMMAND_BODY


@pytest.mark.asyncio
async def test_oversized_mutation_is_bounded_before_operation_effect() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    body = b"x" * 262_145

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=_authorized_headers(),
            content=body,
        )

    # Assert
    assert response.status_code == HTTPStatus.CONTENT_TOO_LARGE
    assert response.json()["errorCode"] == "BODY_TOO_LARGE"
    assert not any(call[0] == "command" for call in operations.calls)


@pytest.mark.asyncio
async def test_live_start_refuses_replay_mode_without_calling_live_operation() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/scenarios/wilderness-missing-person/start",
            headers=_authorized_headers(),
            content=_START_REPLAY_BODY,
        )

    # Assert
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["errorCode"] == "MODE_UNAVAILABLE"
    assert not any(call[0] == "start" for call in operations.calls)


@pytest.mark.asyncio
async def test_replay_composition_starts_session_without_live_writer_capability() -> None:
    # Arrange
    operations = _ReplayOperations()
    app = create_replay_application(_settings(), _context(), _ready(RunMode.REPLAY), operations)

    # Act
    async with _client(app) as client:
        started = await client.post(
            "/api/v1/scenarios/wilderness-missing-person/start",
            headers=_authorized_headers(),
            content=_START_REPLAY_BODY,
        )
        refused = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=_authorized_headers(),
            content=_COMMAND_BODY,
        )

    # Assert
    assert started.status_code == HTTPStatus.ACCEPTED
    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["errorCode"] == "REPLAY_READ_ONLY"
    assert [call[0] for call in operations.calls].count("start-replay") == 1
    assert not hasattr(operations, "command")


@pytest.mark.asyncio
async def test_bootstrap_shell_is_dynamic_no_store_and_assets_are_immutable() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        shell = await client.get("/", headers={"host": "localhost:8080"})
        asset = await client.get("/assets/index-deadbeef.js", headers={"host": "localhost:8080"})

    # Assert
    assert shell.status_code == HTTPStatus.OK
    assert shell.headers["cache-control"] == "no-store"
    assert shell.text.count("current-runtime-bearer") == 1
    assert "data-dashboard-bootstrap" in shell.text
    assert "/assets/index-deadbeef.js" in shell.text
    assert "default-src 'self'" in shell.headers["content-security-policy"]
    assert asset.status_code == HTTPStatus.OK
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_event_route_emits_only_closed_sse_frames_and_closes_stream() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.get("/api/v1/events", headers={"host": "localhost:8080"})

    # Assert
    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("event: snapshot\ndata: {")
    assert "\n\n: keepalive\n\n" in response.text
    assert "event: dashboard-event\n" in response.text
    assert operations.stream.close_calls == 1


@pytest.mark.asyncio
async def test_invalid_asset_name_is_refused_without_calling_asset_loader() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.get("/assets/not-hashed.js", headers={"host": "localhost:8080"})

    # Assert
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["errorCode"] == "ASSET_NOT_FOUND"
    assert not any(call[0] == "asset" for call in operations.calls)


@pytest.mark.asyncio
async def test_malformed_canonical_mutation_returns_typed_error_without_effect() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    repeated_key = b'{"missionId":"mission-a","missionId":"mission-b"}'

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=_authorized_headers(),
            content=repeated_key,
        )

    # Assert
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errorCode"] == "CANONICAL_JSON_INVALID"
    assert not any(call[0] == "command" for call in operations.calls)


@pytest.mark.parametrize(
    ("construct", "message"),
    [
        (
            lambda: ApplicationSettings(
                allowed_hosts=("localhost:8080",),
                allowed_origin="http://localhost:8080",
                asset_entrypoint="index.js",
            ),
            "dashboard asset entrypoint must be content hashed",
        ),
        (
            lambda: JsonOutcome(-1, _schema("error"), _fixture("error")),
            "JSON outcome status is invalid",
        ),
        (
            lambda: JsonOutcome(200, "", _fixture("error")),
            "JSON outcome schema and bytes are required",
        ),
        (
            lambda: JsonOutcome(200, _schema("error"), cast(bytes, "not-response-bytes")),
            "JSON outcome schema and bytes are required",
        ),
    ],
)
def test_application_value_objects_refuse_invalid_runtime_values(
    construct: object, message: str
) -> None:
    # Arrange
    factory = cast(Callable[[], object], construct)

    # Act
    with pytest.raises(ValueError, match=message) as captured:
        factory()

    # Assert
    assert str(captured.value) == message


@pytest.mark.asyncio
async def test_read_routes_delegate_catalog_replay_and_missing_hashed_asset() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        catalog = await client.get("/api/v1/scenarios", headers={"host": "localhost:8080"})
        replay = await client.get(
            "/api/v1/replays/replay-session-synthetic-0001",
            headers={"host": "localhost:8080"},
        )
        missing_asset = await client.get(
            "/assets/other-deadbeef.js", headers={"host": "localhost:8080"}
        )

    # Assert
    assert catalog.content == _fixture("scenario-catalog")
    assert replay.content == _fixture("replay-bundle")
    assert missing_asset.status_code == HTTPStatus.NOT_FOUND
    assert ("replay", "replay-session-synthetic-0001") in operations.calls
    assert ("asset", "other-deadbeef.js") in operations.calls


@pytest.mark.asyncio
async def test_live_reset_and_exact_proposal_decision_reach_distinct_operations() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    decision_body = _fixture("proposal-decision-request")

    # Act
    async with _client(app) as client:
        reset = await client.post(
            "/api/v1/scenarios/current/reset",
            headers=_authorized_headers(),
            content=b"{}",
        )
        decision = await client.post(
            "/api/v1/missions/mission-synthetic-0001/proposals/proposal-synthetic-0001/decisions",
            headers=_authorized_headers(),
            content=decision_body,
        )

    # Assert
    assert reset.content == _fixture("reset-response")
    assert decision.content == _fixture("proposal-decision-response")
    assert [call[0] for call in operations.calls].count("reset") == 1
    assert [call[0] for call in operations.calls].count("decision") == 1


@pytest.mark.asyncio
async def test_replay_reset_uses_only_replay_session_operation() -> None:
    # Arrange
    operations = _ReplayOperations()
    app = create_replay_application(_settings(), _context(), _ready(RunMode.REPLAY), operations)

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/scenarios/current/reset",
            headers=_authorized_headers(),
            content=b"{}",
        )

    # Assert
    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()["mode"] == "replay"
    assert [call[0] for call in operations.calls].count("reset-replay") == 1


@pytest.mark.parametrize(
    ("header_to_remove", "path", "body", "status", "code"),
    [
        (
            "content-type",
            "/api/v1/missions/mission-synthetic-0001/commands",
            _COMMAND_BODY,
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_MEDIA_TYPE",
        ),
        (
            "idempotency-key",
            "/api/v1/missions/mission-synthetic-0001/commands",
            _COMMAND_BODY,
            HTTPStatus.BAD_REQUEST,
            "IDEMPOTENCY_KEY_INVALID",
        ),
        (
            "",
            "/api/v1/missions/mission-other/commands",
            _COMMAND_BODY,
            HTTPStatus.CONFLICT,
            "PATH_BODY_MISMATCH",
        ),
    ],
)
@pytest.mark.asyncio
async def test_mutation_refusal_order_stops_before_typed_operation(
    header_to_remove: str,
    path: str,
    body: bytes,
    status: HTTPStatus,
    code: str,
) -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    headers = _authorized_headers()
    if header_to_remove:
        del headers[header_to_remove]

    # Act
    async with _client(app) as client:
        response = await client.post(path, headers=headers, content=body)

    # Assert
    assert response.status_code == status
    assert response.json()["errorCode"] == code
    assert not any(call[0] == "command" for call in operations.calls)


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        (
            {key: value for key, value in _authorized_headers().items() if key != "origin"},
            "ORIGIN_INVALID",
        ),
        (_authorized_headers() | {"authorization": "Bearer stale"}, "AUTHENTICATION_FAILED"),
    ],
)
@pytest.mark.asyncio
async def test_raw_mutation_authority_maps_origin_and_bearer_to_redacted_errors(
    headers: dict[str, str], code: str
) -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=headers,
            content=_COMMAND_BODY,
        )

    # Assert
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["errorCode"] == code
    assert "stale" not in response.text
    assert not any(call[0] == "command" for call in operations.calls)


@pytest.mark.parametrize("query", ["", "?mode=unknown", "?mode=replay&mode=replay"])
@pytest.mark.asyncio
async def test_readiness_refuses_missing_invalid_or_repeated_mode(query: str) -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.get(f"/api/v1/readiness{query}", headers={"host": "localhost:8080"})

    # Assert
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errorCode"] == "MODE_INVALID"


@pytest.mark.asyncio
async def test_framework_not_found_and_method_refusals_use_closed_error_shape() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        missing = await client.get("/missing", headers={"host": "localhost:8080"})
        wrong_method = await client.delete("/api/v1/health", headers={"host": "localhost:8080"})

    # Assert
    assert missing.status_code == HTTPStatus.NOT_FOUND
    assert missing.json()["errorCode"] == "ROUTE_NOT_FOUND"
    assert wrong_method.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert wrong_method.json()["errorCode"] == "METHOD_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_startup_failure_closes_partial_resources_and_never_becomes_ready() -> None:
    # Arrange
    operations = _FailingOpenOperations()
    readiness = _ready(RunMode.DEGRADED_LIVE)
    app = _live_application(operations, readiness)

    # Act
    with pytest.raises(RuntimeError) as captured:
        async with app.router.lifespan_context(app):
            reached_application = True

    # Assert
    assert str(captured.value) == "synthetic open failure"
    assert operations.calls == [("open", None), ("close", None)]
    assert readiness.phase.name == "STOPPED"
    assert "reached_application" not in locals()


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (
            JsonOutcome(200, _schema("error"), _fixture("error")),
            "route operation returned an unexpected response contract",
        ),
        (
            JsonOutcome(200, _schema("scenario-catalog"), b"not-json"),
            "route operation returned invalid canonical response bytes",
        ),
        (
            JsonOutcome(
                200,
                _schema("scenario-catalog"),
                (_FIXTURES / "scenario-catalog" / "baseline.json").read_bytes(),
            ),
            "route operation returned invalid canonical response bytes",
        ),
        (
            JsonOutcome(
                200,
                _schema("scenario-catalog"),
                b'{"catalogVersion":"scenario-catalog/v1","scenarios":"invalid"}',
            ),
            "route operation returned invalid canonical response bytes",
        ),
    ],
)
@pytest.mark.asyncio
async def test_route_refuses_invalid_operation_response_bytes(
    outcome: JsonOutcome, message: str
) -> None:
    # Arrange
    operations = _ScenarioOutcomeOperations(outcome)
    app = _live_application(operations)

    # Act
    with pytest.raises(RuntimeError) as captured:
        async with _client(app) as client:
            await client.get("/api/v1/scenarios", headers={"host": "localhost:8080"})

    # Assert
    assert str(captured.value) == message


def test_composition_refuses_wrong_mode_or_reused_lifecycle() -> None:
    # Arrange
    replay_readiness = _ready(RunMode.REPLAY)
    live_readiness = _ready(RunMode.DEGRADED_LIVE)
    live_readiness.begin_startup()
    expected = "dashboard readiness does not match the requested composition"

    # Act
    with pytest.raises(ValueError, match=expected) as wrong_mode:
        create_live_application(_settings(), _context(), replay_readiness, _LiveOperations())
    with pytest.raises(ValueError, match=expected) as reused:
        create_live_application(_settings(), _context(), live_readiness, _LiveOperations())

    # Assert
    assert str(wrong_mode.value) == expected
    assert str(reused.value) == expected


@pytest.mark.asyncio
async def test_exact_public_body_bound_is_inclusive_and_canonicalized_before_effect() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)
    body = _COMMAND_BODY + b" " * (262_144 - len(_COMMAND_BODY))

    # Act
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/missions/mission-synthetic-0001/commands",
            headers=_authorized_headers(),
            content=body,
        )

    # Assert
    command = next(call[1] for call in operations.calls if call[0] == "command")
    assert response.status_code == HTTPStatus.ACCEPTED
    assert isinstance(command, AuthorizedMutation)
    assert command.ingress.canonical_body == _COMMAND_BODY


@pytest.mark.asyncio
async def test_terminal_overload_sse_frame_uses_only_its_closed_contract() -> None:
    # Arrange
    operations = _OverloadOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.get("/api/v1/events", headers={"host": "localhost:8080"})

    # Assert
    assert response.text.startswith("event: stream-overloaded\n")
    assert operations.overload_stream.close_calls == 1


@pytest.mark.asyncio
async def test_mutation_before_lifespan_start_is_refused_without_opening_resources() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations, RuntimeReadiness(RunMode.DEGRADED_LIVE))
    transport = httpx.ASGITransport(app=app)

    # Act
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as client:
        response = await client.post(
            "/api/v1/scenarios/current/reset",
            headers=_authorized_headers(),
            content=b"{}",
        )

    # Assert
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["errorCode"] == "NOT_READY"
    assert operations.calls == []


@pytest.mark.asyncio
async def test_live_start_refuses_when_a_required_dependency_is_unavailable() -> None:
    # Arrange
    operations = _LiveOperations()
    readiness = _ready(RunMode.DEGRADED_LIVE)
    app = _live_application(operations, readiness)

    # Act
    async with _client(app) as client:
        readiness.set_dependency(Dependency.SCENARIO_CONTROL, ready=False)
        response = await client.post(
            "/api/v1/scenarios/wilderness-missing-person/start",
            headers=_authorized_headers(),
            content=_START_LIVE_BODY,
        )

    # Assert
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["errorCode"] == "NOT_READY"
    assert not any(call[0] == "start" for call in operations.calls)


@pytest.mark.asyncio
async def test_read_route_refuses_noncanonical_path_identifier_before_operation() -> None:
    # Arrange
    operations = _LiveOperations()
    app = _live_application(operations)

    # Act
    async with _client(app) as client:
        response = await client.get(
            "/api/v1/replays/Replay-Uppercase",
            headers={"host": "localhost:8080"},
        )

    # Assert
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["errorCode"] == "PATH_INVALID"
    assert not any(call[0] == "replay" for call in operations.calls)
