"""Authenticated private FastAPI boundary for scenario run control."""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, cast

from aerial_rescue_contracts import canonical
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ValidationError

from .wire import (
    MAX_SCENARIO_CATALOG_BYTES,
    MAX_WIRE_DOCUMENT_BYTES,
    ScenarioCatalogResponse,
    ScenarioControlCancelRequest,
    ScenarioControlRecoveryRequest,
    ScenarioControlRefusal,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
)

_LOGGER = logging.getLogger(__name__)
_IDENTIFIER_PATTERN: Final = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_HOST_PATTERN: Final = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)(?::(?:[1-9][0-9]{0,4}))?$"
)
_BEARER_PATTERN: Final = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_JSON_MEDIA_TYPE: Final = "application/json"
_NO_STORE: Final = {"Cache-Control": "no-store"}
CANCELLATION_BUDGET_SECONDS: Final = 15.0

type _ResponseModel = ScenarioControlRunStatus | ScenarioCatalogResponse


class ControlRefusal(StrEnum):
    """Closed scenario-control refusal vocabulary."""

    HOST_INVALID = "HOST_INVALID"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    CANONICAL_JSON_INVALID = "CANONICAL_JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PATH_BODY_MISMATCH = "PATH_BODY_MISMATCH"
    RUN_CONFLICT = "RUN_CONFLICT"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    SCENARIO_NOT_FOUND = "SCENARIO_NOT_FOUND"
    SCENARIO_REVISION_MISMATCH = "SCENARIO_REVISION_MISMATCH"
    FLEET_UNAVAILABLE = "FLEET_UNAVAILABLE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


_REFUSAL_STATUS: Final = {
    ControlRefusal.HOST_INVALID: 400,
    ControlRefusal.AUTHENTICATION_FAILED: 401,
    ControlRefusal.UNSUPPORTED_MEDIA_TYPE: 415,
    ControlRefusal.BODY_TOO_LARGE: 413,
    ControlRefusal.CANONICAL_JSON_INVALID: 400,
    ControlRefusal.SCHEMA_INVALID: 422,
    ControlRefusal.PATH_BODY_MISMATCH: 409,
    ControlRefusal.RUN_CONFLICT: 409,
    ControlRefusal.RUN_NOT_FOUND: 404,
    ControlRefusal.CANCELLATION_NOT_ESTABLISHED: 409,
    ControlRefusal.SCENARIO_NOT_FOUND: 404,
    ControlRefusal.SCENARIO_REVISION_MISMATCH: 409,
    ControlRefusal.FLEET_UNAVAILABLE: 503,
    ControlRefusal.INTERNAL_FAILURE: 500,
}
_REFUSAL_MESSAGE: Final = {
    ControlRefusal.HOST_INVALID: "The request Host is not accepted.",
    ControlRefusal.AUTHENTICATION_FAILED: "The private control credential was not accepted.",
    ControlRefusal.UNSUPPORTED_MEDIA_TYPE: "The request must use application/json.",
    ControlRefusal.BODY_TOO_LARGE: "The request body exceeds the accepted bound.",
    ControlRefusal.CANONICAL_JSON_INVALID: "The request is not canonical JSON.",
    ControlRefusal.SCHEMA_INVALID: "The request does not satisfy the closed control schema.",
    ControlRefusal.PATH_BODY_MISMATCH: "The path and request body identify different runs.",
    ControlRefusal.RUN_CONFLICT: "The run identifier is already bound to another request.",
    ControlRefusal.RUN_NOT_FOUND: "The requested run was not found.",
    ControlRefusal.CANCELLATION_NOT_ESTABLISHED: "The run was not confirmed stopped.",
    ControlRefusal.SCENARIO_NOT_FOUND: "The requested scenario was not found.",
    ControlRefusal.SCENARIO_REVISION_MISMATCH: "The requested scenario revision was not found.",
    ControlRefusal.FLEET_UNAVAILABLE: "The fleet control service is unavailable.",
    ControlRefusal.INTERNAL_FAILURE: "The scenario control operation failed.",
}


class ControlError(RuntimeError):
    """An expected refusal from the injected scenario-control authority."""

    def __init__(self, refusal: ControlRefusal, detail: str = "") -> None:
        """Record a closed refusal and optional non-wire diagnostic detail."""
        super().__init__(refusal.value)
        self.refusal = refusal
        self.detail = detail


class ScenarioControl(Protocol):
    """The operation and lifecycle capabilities the private HTTP adapter needs."""

    @property
    def ready(self) -> bool:
        """Whether catalog, fleet, and operation prerequisites are ready."""
        ...

    async def startup(self) -> None:
        """Acquire bounded runtime prerequisites."""
        ...

    async def shutdown(self) -> None:
        """Release runtime prerequisites."""
        ...

    async def catalog(self) -> ScenarioCatalogResponse:
        """Return the dashboard scenario-catalog/v1 projection."""
        ...

    async def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        """Start or reconcile one stable private run."""
        ...

    async def status(self, run_id: str) -> ScenarioControlRunStatus:
        """Return current status for one stable run."""
        ...

    async def recover(self, request: ScenarioControlRecoveryRequest) -> ScenarioControlRunStatus:
        """Reconcile one durable run whose fleet run may be lost."""
        ...

    async def cancel(
        self, request: ScenarioControlCancelRequest, remaining_seconds: float
    ) -> ScenarioControlRunStatus:
        """Cancel one run within the caller's remaining shared budget."""
        ...


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Explicit private-listener admission and lifecycle settings."""

    host: str
    bearer: str = field(repr=False)
    startup_timeout_seconds: float
    shutdown_timeout_seconds: float

    def __post_init__(self) -> None:
        """Refuse ambiguous hosts, weak bearer material, and unbounded lifecycle waits."""
        if _HOST_PATTERN.fullmatch(self.host) is None:
            message = "scenario control host is invalid"
            raise ValueError(message)
        if _BEARER_PATTERN.fullmatch(self.bearer) is None:
            message = "scenario control bearer is too short or outside the safe token grammar"
            raise ValueError(message)
        if self.startup_timeout_seconds <= 0:
            message = "startup timeout must be positive"
            raise ValueError(message)
        if self.shutdown_timeout_seconds <= 0:
            message = "shutdown timeout must be positive"
            raise ValueError(message)


@dataclass(slots=True)
class _Lifecycle:
    accepting: bool = False


def _document_response(model: _ResponseModel, status_code: int) -> Response:
    body = canonical.canonical_bytes(model.model_dump(mode="json", by_alias=True))
    bound = (
        MAX_SCENARIO_CATALOG_BYTES
        if isinstance(model, ScenarioCatalogResponse)
        else MAX_WIRE_DOCUMENT_BYTES
    )
    if len(body) > bound:
        _LOGGER.error("scenario control response exceeds its documented bound")
        return _refusal_response(ControlRefusal.INTERNAL_FAILURE)
    return Response(
        content=body, status_code=status_code, media_type=_JSON_MEDIA_TYPE, headers=_NO_STORE
    )


def _refusal_response(refusal: ControlRefusal) -> Response:
    document = ScenarioControlRefusal.model_validate(
        {
            "controlVersion": 1,
            "errorCode": refusal.value,
            "message": _REFUSAL_MESSAGE[refusal],
        }
    )
    body = canonical.canonical_bytes(document.model_dump(mode="json", by_alias=True))
    return Response(
        content=body,
        status_code=_REFUSAL_STATUS[refusal],
        media_type=_JSON_MEDIA_TYPE,
        headers=_NO_STORE,
    )


def _single_header(request: Request, name: bytes) -> str | None:
    headers = cast("Sequence[tuple[bytes, bytes]]", request.scope["headers"])
    values = [value for key, value in headers if key.lower() == name]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _admit_identity(request: Request, settings: ServerSettings) -> ControlRefusal | None:
    host = _single_header(request, b"host")
    if host is None or not hmac.compare_digest(host, settings.host):
        return ControlRefusal.HOST_INVALID
    authorization = _single_header(request, b"authorization")
    expected = f"Bearer {settings.bearer}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        return ControlRefusal.AUTHENTICATION_FAILED
    return None


async def _bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_WIRE_DOCUMENT_BYTES - len(body):
            raise ControlError(ControlRefusal.BODY_TOO_LARGE)
        body.extend(chunk)
    return bytes(body)


async def _parse_body[RequestT: BaseModel](
    request: Request,
    settings: ServerSettings,
    model: type[RequestT],
) -> RequestT:
    identity_refusal = _admit_identity(request, settings)
    if identity_refusal is not None:
        raise ControlError(identity_refusal)
    media_type = _single_header(request, b"content-type")
    if media_type != _JSON_MEDIA_TYPE:
        raise ControlError(ControlRefusal.UNSUPPORTED_MEDIA_TYPE)
    raw = await _bounded_body(request)
    try:
        value = canonical.decode(raw)
    except canonical.CanonicalizationError as error:
        raise ControlError(ControlRefusal.CANONICAL_JSON_INVALID) from error
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ControlError(ControlRefusal.SCHEMA_INVALID) from error


async def _invoke(
    operation: Awaitable[object],
    success_status: int,
    expected: type[_ResponseModel] = ScenarioControlRunStatus,
) -> Response:
    try:
        result = await operation
    except ControlError as error:
        return _refusal_response(error.refusal)
    except Exception as error:
        redacted = RuntimeError("redacted unexpected scenario control failure")
        _LOGGER.exception(
            "scenario control operation failed",
            exc_info=(type(redacted), redacted, error.__traceback__),
        )
        return _refusal_response(ControlRefusal.INTERNAL_FAILURE)
    if not isinstance(result, expected):
        _LOGGER.error("scenario control operation returned an invalid result type")
        return _refusal_response(ControlRefusal.INTERNAL_FAILURE)
    return _document_response(result, success_status)


class _Handlers:
    """Bound route and lifecycle handlers kept separate from application construction."""

    def __init__(
        self,
        settings: ServerSettings,
        control: ScenarioControl,
        lifecycle: _Lifecycle,
        monotonic: Callable[[], float],
    ) -> None:
        """Bind admission, operations, and readiness to one application epoch."""
        self._settings = settings
        self._control = control
        self._lifecycle = lifecycle
        self._monotonic = monotonic

    @asynccontextmanager
    async def lifespan(self, _application: FastAPI) -> AsyncIterator[None]:
        """Bound startup and shutdown without making import or construction effectful."""
        async with asyncio.timeout(self._settings.startup_timeout_seconds):
            await self._control.startup()
        self._lifecycle.accepting = True
        try:
            yield
        finally:
            self._lifecycle.accepting = False
            async with asyncio.timeout(self._settings.shutdown_timeout_seconds):
                await self._control.shutdown()

    async def health(self, request: Request) -> Response:
        """Report process liveness independently from dependency readiness."""
        if not self._host_is_accepted(request):
            return _refusal_response(ControlRefusal.HOST_INVALID)
        return Response(
            content=canonical.canonical_bytes({"status": "live"}),
            media_type=_JSON_MEDIA_TYPE,
        )

    async def readiness(self, request: Request) -> Response:
        """Report ready only while accepting and every control prerequisite is ready."""
        if not self._host_is_accepted(request):
            return _refusal_response(ControlRefusal.HOST_INVALID)
        ready = self._lifecycle.accepting and self._control.ready
        return Response(
            content=canonical.canonical_bytes({"ready": ready}),
            status_code=200 if ready else 503,
            media_type=_JSON_MEDIA_TYPE,
        )

    async def catalog(self, request: Request) -> Response:
        """Authenticate catalog discovery before consulting the definition source."""
        identity_refusal = _admit_identity(request, self._settings)
        if identity_refusal is not None:
            return _refusal_response(identity_refusal)
        return await _invoke(self._control.catalog(), 200, ScenarioCatalogResponse)

    async def start(self, request: Request) -> Response:
        """Admit, validate, and invoke one stable scenario start."""
        try:
            parsed = await _parse_body(request, self._settings, ScenarioControlStartRequest)
        except ControlError as error:
            return _refusal_response(error.refusal)
        return await _invoke(self._control.start(parsed), 202)

    async def recover(self, request: Request, run_id: str) -> Response:
        """Bind path and body before reconciling one durable run against the fleet."""
        try:
            parsed = await _parse_body(request, self._settings, ScenarioControlRecoveryRequest)
        except ControlError as error:
            return _refusal_response(error.refusal)
        if run_id != parsed.run_id:
            return _refusal_response(ControlRefusal.PATH_BODY_MISMATCH)
        return await _invoke(self._control.recover(parsed), 200)

    async def status(self, request: Request, run_id: str) -> Response:
        """Authenticate before validating and looking up one run identifier."""
        identity_refusal = _admit_identity(request, self._settings)
        if identity_refusal is not None:
            return _refusal_response(identity_refusal)
        if _IDENTIFIER_PATTERN.fullmatch(run_id) is None:
            return _refusal_response(ControlRefusal.SCHEMA_INVALID)
        return await _invoke(self._control.status(run_id), 200)

    async def cancel(self, request: Request, run_id: str) -> Response:
        """Bind path and body before spending only the remaining cancellation budget."""
        started_at = self._monotonic()
        try:
            parsed = await _parse_body(request, self._settings, ScenarioControlCancelRequest)
        except ControlError as error:
            return _refusal_response(error.refusal)
        if run_id != parsed.run_id:
            return _refusal_response(ControlRefusal.PATH_BODY_MISMATCH)
        remaining = CANCELLATION_BUDGET_SECONDS - (self._monotonic() - started_at)
        if remaining <= 0:
            return _refusal_response(ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
        return await _invoke(self._control.cancel(parsed, remaining), 200)

    def _host_is_accepted(self, request: Request) -> bool:
        host = _single_header(request, b"host")
        return host is not None and hmac.compare_digest(host, self._settings.host)


def create_application(
    settings: ServerSettings,
    control: ScenarioControl,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Construct the side-effect-free private scenario-control application."""
    lifecycle = _Lifecycle()
    handlers = _Handlers(settings, control, lifecycle, monotonic)
    application = FastAPI(
        title="Aerial Rescue Scenario Control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=handlers.lifespan,
    )
    application.add_api_route("/healthz", handlers.health, methods=["GET"])
    application.add_api_route("/readyz", handlers.readiness, methods=["GET"])
    application.add_api_route("/internal/v1/scenarios", handlers.catalog, methods=["GET"])
    application.add_api_route("/internal/v1/runs", handlers.start, methods=["POST"])
    application.add_api_route("/internal/v1/runs/{run_id}", handlers.status, methods=["GET"])
    application.add_api_route(
        "/internal/v1/runs/{run_id}/cancel", handlers.cancel, methods=["POST"]
    )
    application.add_api_route(
        "/internal/v1/runs/{run_id}/recover", handlers.recover, methods=["POST"]
    )
    return application
