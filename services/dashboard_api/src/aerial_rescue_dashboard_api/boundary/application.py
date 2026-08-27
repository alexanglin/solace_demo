"""FastAPI composition seam for the private dashboard HTTP boundary."""

from __future__ import annotations

import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal, Never, Protocol, cast

from aerial_rescue_contracts import canonical
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse

from aerial_rescue_dashboard_api.boundary.ingress import (
    MAX_MUTATION_BODY_BYTES,
    MutationIngressError,
    MutationIngressRefusal,
    parse_mutation,
)
from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation
from aerial_rescue_dashboard_api.boundary.security import (
    BoundaryError,
    BoundaryRefusal,
    LocalOperatorBoundary,
)
from aerial_rescue_dashboard_api.boundary.wire import parse_wire_document
from aerial_rescue_dashboard_api.lifecycle import RunMode, RuntimePhase, RuntimeReadiness
from aerial_rescue_dashboard_api.runtime_context import RuntimeContext

_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599
_HEADER_PAIR_LENGTH = 2
_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_HASHED_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*-[A-Za-z0-9_-]{8,}\.[a-z0-9]{1,8}$")
_MUTATION_PATHS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/v1/scenarios/[^/]+/start$",
        r"^/api/v1/scenarios/current/reset$",
        r"^/api/v1/missions/[^/]+/commands$",
        r"^/api/v1/missions/[^/]+/proposals/[^/]+/decisions$",
    )
)
_SECURITY_HEADERS = (
    (
        b"content-security-policy",
        b"default-src 'self'; connect-src 'self'; script-src 'self'; "
        b"style-src 'self'; img-src 'self' data:; frame-src 'none'; "
        b"object-src 'none'; form-action 'none'; base-uri 'none'",
    ),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
)
_SECURITY_HEADER_NAMES = frozenset(name for name, _value in _SECURITY_HEADERS)

type _AsgiMessage = dict[str, object]
type _AsgiScope = dict[str, object]
type _Receive = Callable[[], Awaitable[_AsgiMessage]]
type _Send = Callable[[_AsgiMessage], Awaitable[None]]
type DataEvent = Literal["snapshot", "dashboard-event", "stream-overloaded"]
type AssetMediaType = Literal[
    "application/javascript",
    "font/woff2",
    "image/png",
    "image/svg+xml",
    "text/css",
]


class _AsgiApplication(Protocol):
    async def __call__(self, scope: _AsgiScope, receive: _Receive, send: _Send) -> None: ...


@dataclass(frozen=True)
class ApplicationSettings:
    """Validated non-secret settings for one dashboard API process."""

    allowed_hosts: tuple[str, ...]
    allowed_origin: str
    asset_entrypoint: str

    def __post_init__(self) -> None:
        """Refuse an entrypoint that cannot be an immutable local asset."""
        if _HASHED_ASSET.fullmatch(self.asset_entrypoint) is None:
            message = "dashboard asset entrypoint must be content hashed"
            raise ValueError(message)


@dataclass(frozen=True)
class JsonOutcome:
    """Exact response bytes already selected by an injected operation."""

    status_code: int
    schema_id: str
    body: bytes

    def __post_init__(self) -> None:
        """Keep malformed operation outcomes outside the HTTP adapter."""
        if (
            type(self.status_code) is not int
            or not _MIN_HTTP_STATUS <= self.status_code <= _MAX_HTTP_STATUS
        ):
            message = "JSON outcome status is invalid"
            raise ValueError(message)
        if not self.schema_id or not isinstance(self.body, bytes):
            message = "JSON outcome schema and bytes are required"
            raise ValueError(message)


@dataclass(frozen=True)
class AssetOutcome:
    """One trusted, content-hashed local asset."""

    media_type: AssetMediaType
    body: bytes


@dataclass(frozen=True)
class DataFrame:
    """One canonical SSE data frame from the injected stream owner."""

    event: DataEvent
    body: bytes


@dataclass(frozen=True)
class Keepalive:
    """The sole body-free SSE comment frame."""


type EventFrame = DataFrame | Keepalive


class EventStream(Protocol):
    """A finite-resource event iterator with explicit cancellation cleanup."""

    def __aiter__(self) -> AsyncIterator[EventFrame]:
        """Iterate already classified frames."""
        ...

    async def close(self) -> None:
        """Release this client's stream resources exactly once."""
        ...


class CommonRouteOperations(Protocol):
    """Capabilities shared by live and structurally isolated replay graphs."""

    async def open(self) -> None:
        """Open and validate only resources owned by this composition."""
        ...

    async def close(self) -> None:
        """Close owned resources within their bounded shutdown contract."""
        ...

    async def scenarios(self) -> JsonOutcome:
        """Return the validated scenario catalog."""
        ...

    async def open_events(self) -> EventStream:
        """Allocate one bounded client event stream."""
        ...

    async def replay_bundle(self, session_id: str) -> JsonOutcome:
        """Return one validated, read-only replay bundle."""
        ...

    async def asset(self, asset: str) -> AssetOutcome | None:
        """Return one exact content-hashed local asset when present."""
        ...


class LiveRouteOperations(CommonRouteOperations, Protocol):
    """Writable capabilities that are impossible to pass to replay."""

    async def start_scenario(self, scenario_id: str, mutation: AuthorizedMutation) -> JsonOutcome:
        """Durably start one live scenario."""
        ...

    async def reset(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Durably reset the current live scenario."""
        ...

    async def command(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Durably stage one canonical operator command."""
        ...

    async def decide_proposal(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Durably record one exact proposal decision."""
        ...


class ReplayRouteOperations(CommonRouteOperations, Protocol):
    """Replay-session metadata operations with no application writer port."""

    async def start_replay(self, scenario_id: str, mutation: AuthorizedMutation) -> JsonOutcome:
        """Create one bounded replay session."""
        ...

    async def reset_replay(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Replace one replay session without operational writes."""
        ...


@dataclass(frozen=True)
class _HttpRefusalError(Exception):
    status_code: int
    error_code: str
    message: str


class _LocalBoundaryMiddleware:
    """Authorize raw ASGI headers and attach invariant response controls."""

    def __init__(self, app: object, *, boundary: LocalOperatorBoundary) -> None:
        self._application = cast(_AsgiApplication, app)
        self._boundary = boundary

    async def __call__(self, scope: _AsgiScope, receive: _Receive, send: _Send) -> None:
        """Refuse before routing or body reads, then secure every response."""
        if scope.get("type") != "http":
            await self._application(scope, receive, send)
            return
        secured_send = _security_send(send)
        try:
            authorization = self._boundary.authorize(
                _raw_headers(scope), mutation=_is_mutation(scope)
            )
        except BoundaryError as error:
            refusal = _boundary_http_refusal(error.refusal)
            await _send_error(scope, receive, secured_send, refusal)
            return
        scope["aerial_rescue.operator_id"] = authorization.operator_id
        await self._application(scope, receive, secured_send)


def create_live_application(
    settings: ApplicationSettings,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: LiveRouteOperations,
) -> FastAPI:
    """Construct the live graph with its explicit application writer port."""
    _require_composition_mode(readiness, RunMode.DEGRADED_LIVE)
    app = _base_application(settings, context, readiness, operations)
    _register_live_mutations(app, context, readiness, operations)
    return app


def create_replay_application(
    settings: ApplicationSettings,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: ReplayRouteOperations,
) -> FastAPI:
    """Construct replay without accepting or constructing a live writer port."""
    _require_composition_mode(readiness, RunMode.REPLAY)
    app = _base_application(settings, context, readiness, operations)
    _register_replay_mutations(app, context, readiness, operations)
    return app


def _base_application(
    settings: ApplicationSettings,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: CommonRouteOperations,
) -> FastAPI:
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=_lifespan(readiness, operations),
    )
    boundary = LocalOperatorBoundary(
        allowed_hosts=settings.allowed_hosts,
        allowed_origin=settings.allowed_origin,
        bearer=context.bearer,
        operator_id=context.operator_id,
    )
    app.add_middleware(_LocalBoundaryMiddleware, boundary=boundary)
    _register_error_handlers(app)
    _register_read_routes(app, settings, context, readiness, operations)
    return app


def _lifespan(
    readiness: RuntimeReadiness, operations: CommonRouteOperations
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def manage(_app: FastAPI) -> AsyncIterator[None]:
        readiness.begin_startup()
        try:
            await operations.open()
        except BaseException:
            try:
                await operations.close()
            finally:
                readiness.abort_startup()
            raise
        readiness.activate()
        try:
            yield
        finally:
            readiness.begin_shutdown()
            try:
                await operations.close()
            finally:
                readiness.finish_shutdown()

    return manage


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(_HttpRefusalError)
    async def expected_refusal(_request: Request, error: _HttpRefusalError) -> Response:
        return _error_response(error.status_code, error.error_code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_refusal(_request: Request, _error: RequestValidationError) -> Response:
        return _error_response(
            HTTPStatus.BAD_REQUEST, "REQUEST_INVALID", "request parameters are invalid"
        )

    @app.exception_handler(HTTPStatus.NOT_FOUND)
    async def route_not_found(_request: Request, _error: Exception) -> Response:
        return _error_response(HTTPStatus.NOT_FOUND, "ROUTE_NOT_FOUND", "request was refused")

    @app.exception_handler(HTTPStatus.METHOD_NOT_ALLOWED)
    async def method_not_allowed(_request: Request, _error: Exception) -> Response:
        return _error_response(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "METHOD_NOT_ALLOWED",
            "request was refused",
        )


class _ReadRouteHandlers:
    """Read-only handlers shared without sharing composition resources."""

    def __init__(
        self,
        settings: ApplicationSettings,
        context: RuntimeContext,
        readiness: RuntimeReadiness,
        operations: CommonRouteOperations,
    ) -> None:
        self._settings = settings
        self._context = context
        self._readiness = readiness
        self._operations = operations

    async def health(self) -> Response:
        """Report process liveness independently from dependencies."""
        body = canonical.canonical_bytes(
            {
                "healthVersion": "dashboard-health/v1",
                "status": "alive",
            }
        )
        return _local_json_response(HTTPStatus.OK, _schema("health"), body)

    async def ready(self, request: Request) -> Response:
        """Report whether the requested mode can start."""
        mode = _query_mode(request)
        assessment = self._readiness.assess(mode)
        body = canonical.canonical_bytes(
            {
                "mode": mode.value,
                "readinessVersion": "dashboard-readiness/v1",
                "ready": assessment.ready,
                "reasons": list(assessment.reasons),
            }
        )
        return _local_json_response(HTTPStatus.OK, _schema("readiness"), body)

    async def scenarios(self) -> Response:
        """Serve the injected, validated scenario catalog."""
        outcome = await self._operations.scenarios()
        return _operation_json_response(outcome, HTTPStatus.OK, _schema("scenario-catalog"))

    async def events(self) -> StreamingResponse:
        """Open one finite-resource normalized event stream."""
        stream = await self._operations.open_events()
        return StreamingResponse(
            _stream_bytes(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def replay(self, request: Request) -> Response:
        """Serve one read-only validated replay bundle."""
        session_id = _path_identifier(request, "sessionId")
        outcome = await self._operations.replay_bundle(session_id)
        return _operation_json_response(outcome, HTTPStatus.OK, _schema("replay-bundle"))

    async def shell(self) -> Response:
        """Serve a fresh no-store bootstrap document."""
        return Response(
            _bootstrap_shell(self._context, self._settings.asset_entrypoint),
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def asset(self, request: Request) -> Response:
        """Serve one content-hashed local asset."""
        asset_name = _path_value(request, "asset")
        if _HASHED_ASSET.fullmatch(asset_name) is None:
            raise _HttpRefusalError(HTTPStatus.NOT_FOUND, "ASSET_NOT_FOUND", "asset was not found")
        outcome = await self._operations.asset(asset_name)
        if outcome is None:
            raise _HttpRefusalError(HTTPStatus.NOT_FOUND, "ASSET_NOT_FOUND", "asset was not found")
        return Response(
            outcome.body,
            media_type=outcome.media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )


def _register_read_routes(
    app: FastAPI,
    settings: ApplicationSettings,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: CommonRouteOperations,
) -> None:
    handlers = _ReadRouteHandlers(settings, context, readiness, operations)
    app.add_api_route("/api/v1/health", handlers.health, methods=["GET"], response_model=None)
    app.add_api_route("/api/v1/readiness", handlers.ready, methods=["GET"], response_model=None)
    app.add_api_route("/api/v1/scenarios", handlers.scenarios, methods=["GET"], response_model=None)
    app.add_api_route("/api/v1/events", handlers.events, methods=["GET"], response_model=None)
    app.add_api_route(
        "/api/v1/replays/{sessionId}",
        handlers.replay,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route("/", handlers.shell, methods=["GET"], response_model=None)
    app.add_api_route("/assets/{asset}", handlers.asset, methods=["GET"], response_model=None)


def _register_live_mutations(
    app: FastAPI,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: LiveRouteOperations,
) -> None:
    @app.post("/api/v1/scenarios/{scenarioId}/start", response_model=None)
    async def start(request: Request) -> Response:
        _require_ready_to_start(readiness, RunMode.DEGRADED_LIVE)
        scenario_id = _path_identifier(request, "scenarioId")
        mutation = await _authorized_mutation(request, _schema("start-request"), {}, context)
        _require_request_mode(mutation, RunMode.DEGRADED_LIVE)
        outcome = await operations.start_scenario(scenario_id, mutation)
        return _operation_json_response(outcome, 202, _schema("start-response"))

    @app.post("/api/v1/scenarios/current/reset", response_model=None)
    async def reset(request: Request) -> Response:
        _require_mutations_open(readiness)
        mutation = await _authorized_mutation(request, _schema("reset-request"), {}, context)
        outcome = await operations.reset(mutation)
        return _operation_json_response(outcome, 202, _schema("reset-response"))

    @app.post("/api/v1/missions/{missionId}/commands", response_model=None)
    async def command(request: Request) -> Response:
        _require_mutations_open(readiness)
        mission_id = _path_value(request, "missionId")
        mutation = await _authorized_mutation(
            request,
            _schema("operator-command-request"),
            {"mission_id": mission_id},
            context,
        )
        outcome = await operations.command(mutation)
        return _operation_json_response(outcome, 202, _schema("command-response"))

    @app.post(
        "/api/v1/missions/{missionId}/proposals/{proposalId}/decisions",
        response_model=None,
    )
    async def decision(request: Request) -> Response:
        _require_mutations_open(readiness)
        bindings = {
            "mission_id": _path_value(request, "missionId"),
            "proposal_id": _path_value(request, "proposalId"),
        }
        mutation = await _authorized_mutation(
            request, _schema("proposal-decision-request"), bindings, context
        )
        outcome = await operations.decide_proposal(mutation)
        return _operation_json_response(outcome, 202, _schema("proposal-decision-response"))


def _register_replay_mutations(
    app: FastAPI,
    context: RuntimeContext,
    readiness: RuntimeReadiness,
    operations: ReplayRouteOperations,
) -> None:
    @app.post("/api/v1/scenarios/{scenarioId}/start", response_model=None)
    async def start(request: Request) -> Response:
        _require_ready_to_start(readiness, RunMode.REPLAY)
        scenario_id = _path_identifier(request, "scenarioId")
        mutation = await _authorized_mutation(request, _schema("start-request"), {}, context)
        _require_request_mode(mutation, RunMode.REPLAY)
        outcome = await operations.start_replay(scenario_id, mutation)
        return _operation_json_response(outcome, 202, _schema("start-response"))

    @app.post("/api/v1/scenarios/current/reset", response_model=None)
    async def reset(request: Request) -> Response:
        _require_mutations_open(readiness)
        mutation = await _authorized_mutation(request, _schema("reset-request"), {}, context)
        outcome = await operations.reset_replay(mutation)
        return _operation_json_response(outcome, 202, _schema("reset-response"))

    async def replay_read_only() -> Response:
        _require_mutations_open(readiness)
        raise _HttpRefusalError(409, "REPLAY_READ_ONLY", "replay cannot mutate operational state")

    app.add_api_route(
        "/api/v1/missions/{missionId}/commands",
        replay_read_only,
        methods=["POST"],
        response_model=None,
    )
    app.add_api_route(
        "/api/v1/missions/{missionId}/proposals/{proposalId}/decisions",
        replay_read_only,
        methods=["POST"],
        response_model=None,
    )


async def _authorized_mutation(
    request: Request,
    schema_id: str,
    path_bindings: Mapping[str, str],
    context: RuntimeContext,
) -> AuthorizedMutation:
    content_type = _single_header(request, b"content-type")
    if content_type != "application/json":
        _raise_ingress(MutationIngressRefusal.MEDIA_TYPE)
    body = await _bounded_body(request)
    idempotency_key = _single_header(request, b"idempotency-key")
    try:
        ingress = parse_mutation(
            schema_id=schema_id,
            body=body,
            content_type=content_type,
            idempotency_key=idempotency_key,
            path_bindings=path_bindings,
        )
    except MutationIngressError as error:
        _raise_ingress(error.refusal)
    operator_id = request.scope.get("aerial_rescue.operator_id")
    if operator_id != context.operator_id:
        raise _HttpRefusalError(401, "AUTHENTICATION_FAILED", "authorization is not current")
    return AuthorizedMutation(ingress, context.operator_id)


async def _bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_MUTATION_BODY_BYTES:
            _raise_ingress(MutationIngressRefusal.BODY_TOO_LARGE)
        body.extend(chunk)
    return bytes(body)


def _raise_ingress(refusal: MutationIngressRefusal) -> Never:
    status = 400
    if refusal is MutationIngressRefusal.MEDIA_TYPE:
        status = 415
    elif refusal is MutationIngressRefusal.BODY_TOO_LARGE:
        status = 413
    elif refusal is MutationIngressRefusal.PATH_BODY_MISMATCH:
        status = 409
    raise _HttpRefusalError(status, _ingress_code(refusal), refusal.value)


def _ingress_code(refusal: MutationIngressRefusal) -> str:
    return {
        MutationIngressRefusal.MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
        MutationIngressRefusal.BODY_TOO_LARGE: "BODY_TOO_LARGE",
        MutationIngressRefusal.IDEMPOTENCY_KEY: "IDEMPOTENCY_KEY_INVALID",
        MutationIngressRefusal.CANONICAL_JSON: "CANONICAL_JSON_INVALID",
        MutationIngressRefusal.SCHEMA: "SCHEMA_INVALID",
        MutationIngressRefusal.PATH_BODY_MISMATCH: "PATH_BODY_MISMATCH",
    }[refusal]


def _require_mutations_open(readiness: RuntimeReadiness) -> None:
    if not readiness.accepting_mutations:
        raise _HttpRefusalError(503, "NOT_READY", "runtime is not accepting mutations")


def _require_ready_to_start(readiness: RuntimeReadiness, mode: RunMode) -> None:
    _require_mutations_open(readiness)
    if not readiness.assess(mode).ready:
        raise _HttpRefusalError(503, "NOT_READY", "selected mode cannot start")


def _require_request_mode(mutation: AuthorizedMutation, expected: RunMode) -> None:
    document = cast(Mapping[str, object], mutation.ingress.document.model_dump(by_alias=True))
    if document.get("mode") != expected.value:
        raise _HttpRefusalError(409, "MODE_UNAVAILABLE", "selected mode is unavailable")


def _operation_json_response(
    outcome: JsonOutcome, success_status: int, success_schema: str
) -> Response:
    error_schema = _schema("error")
    expected_schema = success_schema if outcome.status_code == success_status else error_schema
    if outcome.schema_id != expected_schema:
        message = "route operation returned an unexpected response contract"
        raise RuntimeError(message)
    return _local_json_response(outcome.status_code, outcome.schema_id, outcome.body)


def _local_json_response(status_code: int, schema_id: str, body: bytes) -> Response:
    _require_canonical_document(schema_id, body)
    return Response(
        body,
        status_code=status_code,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _require_canonical_document(schema_id: str, body: bytes) -> None:
    try:
        value = canonical.decode(body)
    except canonical.CanonicalizationError:
        _raise_invalid_response()
    if canonical.canonical_bytes(value) != body:
        _raise_invalid_response()
    try:
        parse_wire_document(schema_id, body)
    except ValueError:
        _raise_invalid_response()


def _raise_invalid_response() -> Never:
    message = "route operation returned invalid canonical response bytes"
    raise RuntimeError(message) from None


def _error_response(status_code: int, error_code: str, message: str) -> Response:
    body = canonical.canonical_bytes(
        {
            "errorCode": error_code,
            "errorVersion": "dashboard-error/v1",
            "message": message,
        }
    )
    return _local_json_response(status_code, _schema("error"), body)


async def _stream_bytes(stream: EventStream) -> AsyncIterator[bytes]:
    try:
        async for frame in stream:
            yield _encode_frame(frame)
    finally:
        await stream.close()


def _encode_frame(frame: EventFrame) -> bytes:
    if isinstance(frame, Keepalive):
        return b": keepalive\n\n"
    schema_id = {
        "snapshot": _schema("dashboard-snapshot"),
        "dashboard-event": _schema("dashboard-event-frame"),
        "stream-overloaded": _schema("stream-overloaded"),
    }[frame.event]
    _require_canonical_document(schema_id, frame.body)
    return b"event: " + frame.event.encode("ascii") + b"\ndata: " + frame.body + b"\n\n"


def _bootstrap_shell(context: RuntimeContext, asset_entrypoint: str) -> bytes:
    bootstrap = canonical.canonical_bytes(
        {
            "bearer": context.bearer,
            "bootstrapVersion": "dashboard-bootstrap/v1",
            "runtimeId": context.runtime_id,
        }
    )
    _require_canonical_document(_schema("bootstrap"), bootstrap)
    return (
        b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        b'<meta name="viewport" content="width=device-width,initial-scale=1">'
        b'<title>Aerial Rescue Mesh</title></head><body><div id="root"></div>'
        b'<script type="application/json" data-dashboard-bootstrap>'
        + bootstrap
        + b'</script><script type="module" src="/assets/'
        + asset_entrypoint.encode("ascii")
        + b'"></script></body></html>'
    )


def _query_mode(request: Request) -> RunMode:
    values = request.query_params.getlist("mode")
    if len(values) != 1:
        raise _HttpRefusalError(400, "MODE_INVALID", "exactly one mode is required")
    try:
        return RunMode(values[0])
    except ValueError:
        raise _HttpRefusalError(400, "MODE_INVALID", "mode is invalid") from None


def _path_identifier(request: Request, name: str) -> str:
    value = _path_value(request, name)
    if _IDENTIFIER.fullmatch(value) is None:
        raise _HttpRefusalError(400, "PATH_INVALID", "path identifier is invalid")
    return value


def _path_value(request: Request, name: str) -> str:
    value = request.path_params.get(name)
    if not isinstance(value, str):
        raise _HttpRefusalError(400, "PATH_INVALID", "path parameter is invalid")
    return value


def _single_header(request: Request, name: bytes) -> str | None:
    values = tuple(
        value for key, value in _raw_headers(cast(_AsgiScope, request.scope)) if key == name
    )
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None


def _schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}{name}.schema.json"


def _require_composition_mode(readiness: RuntimeReadiness, expected: RunMode) -> None:
    if readiness.mode is not expected or readiness.phase is not RuntimePhase.CREATED:
        message = "dashboard readiness does not match the requested composition"
        raise ValueError(message)


def _is_mutation(scope: Mapping[str, object]) -> bool:
    if scope.get("method") != "POST":
        return False
    path = scope.get("path")
    return isinstance(path, str) and any(pattern.fullmatch(path) for pattern in _MUTATION_PATHS)


def _raw_headers(scope: Mapping[str, object]) -> tuple[tuple[bytes, bytes], ...]:
    value = scope.get("headers")
    if not isinstance(value, Sequence):
        return ()
    headers: list[tuple[bytes, bytes]] = []
    for item in value:
        if not isinstance(item, Sequence) or len(item) != _HEADER_PAIR_LENGTH:
            continue
        key, header_value = item
        if isinstance(key, bytes) and isinstance(header_value, bytes):
            headers.append((key.lower(), header_value))
    return tuple(headers)


def _boundary_http_refusal(refusal: BoundaryRefusal) -> _HttpRefusalError:
    if refusal.name.startswith("HOST_"):
        return _HttpRefusalError(HTTPStatus.BAD_REQUEST, "HOST_INVALID", refusal.value)
    if refusal.name.startswith("ORIGIN_"):
        return _HttpRefusalError(HTTPStatus.UNAUTHORIZED, "ORIGIN_INVALID", refusal.value)
    return _HttpRefusalError(HTTPStatus.UNAUTHORIZED, "AUTHENTICATION_FAILED", refusal.value)


def _security_send(send: _Send) -> _Send:
    async def secured(message: _AsgiMessage) -> None:
        if message.get("type") == "http.response.start":
            updated = dict(message)
            headers = _message_headers(message)
            updated["headers"] = [
                (name, value)
                for name, value in headers
                if name.lower() not in _SECURITY_HEADER_NAMES
            ] + list(_SECURITY_HEADERS)
            await send(updated)
            return
        await send(message)

    return secured


def _message_headers(message: Mapping[str, object]) -> list[tuple[bytes, bytes]]:
    value = message.get("headers")
    if not isinstance(value, Sequence):
        return []
    return [
        (key, header_value)
        for item in value
        if isinstance(item, Sequence) and len(item) == _HEADER_PAIR_LENGTH
        for key, header_value in (item,)
        if isinstance(key, bytes) and isinstance(header_value, bytes)
    ]


async def _send_error(
    scope: _AsgiScope,
    receive: _Receive,
    send: _Send,
    refusal: _HttpRefusalError,
) -> None:
    response = _error_response(refusal.status_code, refusal.error_code, refusal.message)
    application = cast(_AsgiApplication, response)
    await application(scope, receive, send)
