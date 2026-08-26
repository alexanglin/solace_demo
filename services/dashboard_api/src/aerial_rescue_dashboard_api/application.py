"""Injected FastAPI runtime for the closed public Wilderness Dashboard surface."""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Final, cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import Context, digest
from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, Response, StreamingResponse

from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.delivery.assets import AssetCatalog
from aerial_rescue_dashboard_api.delivery.openapi import install_openapi
from aerial_rescue_dashboard_api.documents import (
    CATALOG_SCHEMA,
    REPLAY_SCHEMA,
    canonical_validated_bytes,
    validated_document,
)
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.orchestration import (
    CATALOG_MAX_BYTES,
    REPLAY_MAX_BYTES,
    OperationCoordinator,
)
from aerial_rescue_dashboard_api.ports import (
    IdentifierSource,
    RecorderReadinessPort,
    ReplayPort,
    ResourcePort,
    RunMode,
    ScenarioPort,
    StorePort,
)
from aerial_rescue_dashboard_api.responses import (
    IMMUTABLE,
    NO_STORE,
    error_response,
    exact_json,
    json_document,
)
from aerial_rescue_dashboard_api.security import AdmissionMiddleware, _origin_tuple
from aerial_rescue_dashboard_api.snapshot import SnapshotService
from aerial_rescue_dashboard_api.stream import EventStreamer, native_last_event_id
from aerial_rescue_dashboard_api.wire import parse_wire_document

PUBLIC_BODY_BYTES: Final = 4 * 1024
MAXIMUM_CANONICAL_DEPTH: Final = 16
_SECRET_BYTES: Final = 32
_NOT_FOUND: Final = 404
_METHOD_NOT_ALLOWED: Final = 405
_START_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/start-request.schema.json"
)
_RESET_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-request.schema.json"
)
_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_IDEMPOTENCY_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_BOOTSTRAP_PLACEHOLDER: Final = "<!--DASHBOARD_BOOTSTRAP-->"


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated process-lifetime identity, admission, shell, and cursor settings."""

    runtime_id: str
    bearer: str
    allowed_hosts: frozenset[str]
    dashboard_origin: str
    cursor_key: bytes
    index_template: str
    assets: AssetCatalog

    def __post_init__(self) -> None:
        """Fail startup before accepting requests when immutable settings are malformed."""
        if _IDENTIFIER.fullmatch(self.runtime_id) is None:
            message = "runtime identifier is invalid"
            raise ValueError(message)
        if not self.bearer or len(self.cursor_key) != _SECRET_BYTES:
            message = "runtime credentials are invalid"
            raise ValueError(message)
        if not self.allowed_hosts:
            message = "Host allowlist cannot be empty"
            raise ValueError(message)
        if _origin_tuple(self.dashboard_origin) is None:
            message = "dashboard origin must be an exact HTTP origin with an explicit port"
            raise ValueError(message)
        if self.index_template.count(_BOOTSTRAP_PLACEHOLDER) != 1:
            message = "index template must contain one bootstrap placeholder"
            raise ValueError(message)


@dataclass(frozen=True)
class ApplicationPorts:
    """Every side-effecting dependency required by the dashboard application."""

    store: StorePort
    scenario: ScenarioPort
    replay: ReplayPort
    recorder: RecorderReadinessPort
    identifiers: IdentifierSource
    resources: ResourcePort | None = None


@dataclass(frozen=True)
class MutationInput:
    """Canonical-decoded and strict-schema-validated mutation input."""

    idempotency_key: str
    document: Mapping[str, object]
    request_digest: str


class SecureIdentifiers:
    """Default cryptographic UUIDv4-backed stable operation identity source."""

    def new(self, kind: str) -> str:
        """Return a schema-safe namespace plus fresh random UUID hex."""
        return f"{kind}-{uuid.uuid4().hex}"


def fresh_runtime_settings(
    *,
    allowed_hosts: frozenset[str],
    dashboard_origin: str,
    index_template: str,
    assets: AssetCatalog,
) -> RuntimeSettings:
    """Generate independent bearer and cursor secrets for one process lifetime."""
    return RuntimeSettings(
        runtime_id=f"runtime-{uuid.uuid4().hex}",
        bearer=secrets.token_urlsafe(32),
        allowed_hosts=allowed_hosts,
        dashboard_origin=dashboard_origin,
        cursor_key=secrets.token_bytes(32),
        index_template=index_template,
        assets=assets,
    )


def create_app(settings: RuntimeSettings, ports: ApplicationPorts) -> FastAPI:
    """Create the side-effect-free injected FastAPI application graph."""
    coordinator = OperationCoordinator(
        ports.store,
        ports.scenario,
        ports.replay,
        ports.identifiers,
    )
    snapshots = SnapshotService(
        ports.store,
        CursorCodec(settings.runtime_id, settings.cursor_key),
        settings.runtime_id,
    )
    streams = EventStreamer(ports.store, snapshots)

    app = FastAPI(
        title="Aerial Rescue Wilderness Dashboard API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan(coordinator, ports.resources),
    )
    routes = _DashboardRoutes(settings, ports, coordinator, streams)
    _register_exception_handlers(app)
    _register_routes(app, routes)
    install_openapi(app)
    app.add_middleware(
        AdmissionMiddleware,
        allowed_hosts=settings.allowed_hosts,
        dashboard_origin=settings.dashboard_origin,
        bearer=settings.bearer,
    )
    return app


def _lifespan(
    coordinator: OperationCoordinator,
    resources: ResourcePort | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Return a lifespan context that reconciles durable pending work before readiness."""

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        try:
            await coordinator.reconcile_pending()
            yield
        finally:
            if resources is not None:
                await resources.close()

    return lifespan


def _register_exception_handlers(app: FastAPI) -> None:
    """Replace framework error bodies with the closed dashboard refusal schema."""

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, error: ApiError) -> Response:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, _error: RequestValidationError) -> Response:
        return error_response(ErrorCode.SCHEMA_INVALID)

    @app.exception_handler(HTTPException)
    async def http_handler(_request: Request, error: HTTPException) -> Response:
        if error.status_code == _METHOD_NOT_ALLOWED:
            return error_response(ErrorCode.METHOD_NOT_ALLOWED)
        if error.status_code == _NOT_FOUND:
            return error_response(ErrorCode.ASSET_NOT_FOUND)
        return error_response(ErrorCode.INTERNAL_FAILURE)

    @app.exception_handler(Exception)
    async def unexpected_handler(_request: Request, _error: Exception) -> Response:
        return error_response(ErrorCode.INTERNAL_FAILURE)


class _DashboardRoutes:
    """Cohesive route adapters over the injected application services."""

    def __init__(
        self,
        settings: RuntimeSettings,
        ports: ApplicationPorts,
        coordinator: OperationCoordinator,
        streams: EventStreamer,
    ) -> None:
        """Retain immutable settings and typed runtime collaborators."""
        self._settings = settings
        self._ports = ports
        self._coordinator = coordinator
        self._streams = streams

    async def health(self) -> Response:
        """Report process liveness without duplicating runtime bootstrap state."""
        return json_document(
            {
                "healthVersion": "dashboard-health/v1",
                "status": "alive",
            }
        )

    async def readiness(self, request: Request) -> Response:
        """Evaluate only the dependency graph required by the selected mode."""
        mode = _readiness_mode(request)
        reasons = list(await self._ports.store.readiness())
        if mode is RunMode.DEGRADED_LIVE:
            reasons.extend(await self._ports.scenario.readiness())
            reasons.extend(await self._ports.recorder.readiness())
        else:
            reasons.extend(await self._ports.replay.readiness())
        bounded = reasons[:20]
        return json_document(
            {
                "mode": mode.value,
                "readinessVersion": "dashboard-readiness/v1",
                "ready": not bounded,
                "reasons": bounded,
            },
            200 if not bounded else 503,
        )

    async def scenarios(self) -> Response:
        """Return the canonical validated private scenario catalog projection."""
        raw = await self._ports.scenario.catalog()
        return exact_json(
            canonical_validated_bytes(CATALOG_SCHEMA, raw, maximum_bytes=CATALOG_MAX_BYTES)
        )

    async def start_scenario(
        self,
        request: Request,
        scenario_id: str = Path(alias="scenarioId"),
    ) -> Response:
        """Validate and durably orchestrate one live or replay start."""
        if _IDENTIFIER.fullmatch(scenario_id) is None:
            raise ApiError(ErrorCode.SCHEMA_INVALID)
        accepted = await _mutation_input(request, _START_SCHEMA, "start", scenario_id)
        mode_value = accepted.document.get("mode")
        revision_value = accepted.document.get("scenarioRevision")
        if not isinstance(mode_value, str) or not isinstance(revision_value, int):
            raise ApiError(ErrorCode.SCHEMA_INVALID)
        answer = await self._coordinator.start(
            scenario_id,
            RunMode(mode_value),
            revision_value,
            accepted.idempotency_key,
            accepted.request_digest,
        )
        return exact_json(answer.body, answer.status)

    async def reset_scenario(self, request: Request) -> Response:
        """Reset through bounded authoritative cancellation while retaining history."""
        accepted = await _mutation_input(request, _RESET_SCHEMA, "reset", None)
        answer = await self._coordinator.reset(
            accepted.idempotency_key,
            accepted.request_digest,
        )
        return exact_json(answer.body, answer.status)

    async def events(self, request: Request) -> StreamingResponse:
        """Open a finite native-cursor event stream or return capacity refusal."""
        values = _raw_headers(request, b"last-event-id")
        cursor = native_last_event_id(values)
        handle = await self._streams.open(cursor)
        return StreamingResponse(
            handle.body(),
            media_type="text/event-stream",
            headers={"Cache-Control": NO_STORE},
        )

    async def replay(
        self,
        session_id: str = Path(alias="sessionId"),
    ) -> Response:
        """Serve the validator's exact bytes for a known session association."""
        if _IDENTIFIER.fullmatch(session_id) is None:
            raise ApiError(ErrorCode.REPLAY_SESSION_NOT_FOUND)
        raw = await self._ports.replay.bundle(session_id)
        if raw is None:
            raise ApiError(ErrorCode.REPLAY_SESSION_NOT_FOUND)
        validated_document(REPLAY_SCHEMA, raw, maximum_bytes=REPLAY_MAX_BYTES)
        return exact_json(raw)

    async def index(self) -> HTMLResponse:
        """Inject one process-lifetime bootstrap into the no-store HTML shell."""
        bootstrap = canonical.canonical_bytes(
            {
                "bearer": self._settings.bearer,
                "bootstrapVersion": "dashboard-bootstrap/v1",
                "runtimeId": self._settings.runtime_id,
            }
        ).decode()
        safe_bootstrap = (
            bootstrap.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        )
        element = (
            '<script id="dashboard-bootstrap" type="application/json">'
            + safe_bootstrap
            + "</script>"
        )
        body = self._settings.index_template.replace(_BOOTSTRAP_PLACEHOLDER, element)
        return HTMLResponse(body, headers={"Cache-Control": NO_STORE})

    async def asset(self, asset: str) -> Response:
        """Return only an exact immutable manifest asset."""
        selected = self._settings.assets.get(asset)
        if selected is None:
            raise ApiError(ErrorCode.ASSET_NOT_FOUND)
        return Response(
            selected.body,
            media_type=selected.media_type,
            headers={
                "Cache-Control": IMMUTABLE,
                "ETag": selected.etag,
            },
        )


def _register_routes(app: FastAPI, routes: _DashboardRoutes) -> None:
    """Register exactly the accepted public route inventory and no diagnostic routes."""
    app.get("/api/v1/health")(routes.health)
    app.get("/api/v1/readiness")(routes.readiness)
    app.get("/api/v1/scenarios")(routes.scenarios)
    app.post("/api/v1/scenarios/{scenarioId}/start")(routes.start_scenario)
    app.post("/api/v1/scenarios/current/reset")(routes.reset_scenario)
    app.get("/api/v1/events")(routes.events)
    app.get("/api/v1/replays/{sessionId}")(routes.replay)
    app.get("/")(routes.index)
    app.get("/assets/{asset}")(routes.asset)


async def _mutation_input(
    request: Request,
    schema_id: str,
    operation: str,
    scenario_id: str | None,
) -> MutationInput:
    """Apply media/body/key/canonical/schema admission in the accepted strict order."""
    _require_media_type(request)
    body = await _bounded_body(request)
    key = _require_idempotency_key(request)
    decoded = _decode_canonical_body(body)
    _validate_request_schema(schema_id, body)
    if not isinstance(decoded, Mapping):
        raise ApiError(ErrorCode.SCHEMA_INVALID)
    document = cast(Mapping[str, object], decoded)
    return MutationInput(
        key,
        document,
        _mutation_digest(operation, scenario_id, document),
    )


def _require_media_type(request: Request) -> None:
    """Require exactly one application/json media type before body consumption."""
    content_types = _raw_headers(request, b"content-type")
    json_media = b"application/json"
    if len(content_types) != 1 or content_types[0].split(b";", 1)[0].strip().lower() != json_media:
        raise ApiError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)


def _require_idempotency_key(request: Request) -> str:
    """Require exactly one lowercase RFC 4122 UUID version-four spelling."""
    keys = _raw_headers(request, b"idempotency-key")
    if len(keys) != 1:
        raise ApiError(ErrorCode.IDEMPOTENCY_KEY_INVALID)
    try:
        key = keys[0].decode("ascii")
    except UnicodeDecodeError as invalid:
        raise ApiError(ErrorCode.IDEMPOTENCY_KEY_INVALID) from invalid
    if _IDEMPOTENCY_KEY.fullmatch(key) is None:
        raise ApiError(ErrorCode.IDEMPOTENCY_KEY_INVALID)
    return key


def _decode_canonical_body(body: bytes) -> object:
    """Decode duplicate-free integer-only JSON and enforce the depth bound."""
    try:
        decoded = canonical.decode(body)
    except (ValueError, RecursionError) as invalid:
        raise ApiError(ErrorCode.CANONICAL_JSON_INVALID) from invalid
    if _depth(decoded) > MAXIMUM_CANONICAL_DEPTH:
        raise ApiError(ErrorCode.CANONICAL_JSON_INVALID)
    return decoded


def _validate_request_schema(schema_id: str, body: bytes) -> None:
    """Apply the strict service-local Pydantic twin after canonical decoding."""
    try:
        parse_wire_document(schema_id, body)
    except (ValueError, ValidationError) as invalid:
        raise ApiError(ErrorCode.SCHEMA_INVALID) from invalid


def _mutation_digest(
    operation: str,
    scenario_id: str | None,
    document: Mapping[str, object],
) -> str:
    """Bind idempotency content to operation, path scenario, and canonical request body."""
    covered: dict[str, object] = {
        "canonicalizationVersion": 1,
        "operation": operation,
        "request": dict(document),
    }
    if scenario_id is not None:
        covered["scenarioId"] = scenario_id
    return digest(Context.IDEMPOTENCY_BODY, covered)


async def _bounded_body(request: Request) -> bytes:
    """Stop consuming the body as soon as the public 4 KiB limit is exceeded."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > PUBLIC_BODY_BYTES:
            raise ApiError(ErrorCode.BODY_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _depth(value: object) -> int:
    """Measure canonical container nesting iteratively within the already bounded body."""
    maximum = 0
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(item, Mapping):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            pending.extend((child, depth + 1) for child in item)
    return maximum


def _readiness_mode(request: Request) -> RunMode:
    """Refuse missing, repeated, or out-of-vocabulary query input without a 422."""
    values = request.query_params.getlist("mode")
    if len(values) != 1:
        raise ApiError(ErrorCode.SCHEMA_INVALID)
    try:
        return RunMode(values[0])
    except ValueError as invalid:
        raise ApiError(ErrorCode.SCHEMA_INVALID) from invalid


def _raw_headers(request: Request, name: bytes) -> tuple[bytes, ...]:
    """Preserve repeated header fields instead of accepting a comma-joined value."""
    return tuple(value for header, value in request.scope["headers"] if header.lower() == name)
