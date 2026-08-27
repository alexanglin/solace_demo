"""Authenticated private FastAPI boundary for fleet run control."""

from __future__ import annotations

import asyncio
import hmac
import re
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, cast

from aerial_rescue_contracts import canonical
from fastapi import FastAPI, Request, Response
from pydantic import ValidationError

from aerial_rescue_fleet_simulator.control_wire import (
    MAXIMUM_WIRE_BYTES,
    FleetControlCancelRequest,
    FleetControlRefusal,
    FleetControlRunStatus,
    FleetControlStartRequest,
)

_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::[1-9][0-9]{0,4})?$")
_BEARER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_JSON: Final = "application/json"


class ControlRefusal(StrEnum):
    """Closed fleet-control refusal vocabulary."""

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
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    RUN_FAILED = "RUN_FAILED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


_STATUS: Final = {
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
    ControlRefusal.CAPACITY_EXCEEDED: 503,
    ControlRefusal.RUN_FAILED: 500,
    ControlRefusal.INTERNAL_FAILURE: 500,
}
_MESSAGE: Final = {
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
    ControlRefusal.CAPACITY_EXCEEDED: "The fleet has no capacity for another run.",
    ControlRefusal.RUN_FAILED: "The fleet run failed.",
    ControlRefusal.INTERNAL_FAILURE: "The fleet control operation failed.",
}


class ControlError(RuntimeError):
    """An expected refusal from the injected fleet-control authority."""

    def __init__(self, refusal: ControlRefusal) -> None:
        """Retain only the closed refusal code."""
        super().__init__(refusal.value)
        self.refusal = refusal


class FleetControl(Protocol):
    """The operations and lifecycle the private HTTP adapter needs."""

    @property
    def ready(self) -> bool:
        """Return whether broker, store, queues, and recovery are ready."""

    async def startup(self) -> None:
        """Acquire bounded runtime prerequisites."""

    async def shutdown(self) -> None:
        """Cancel work and release owned resources."""

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start or reconcile one stable run."""

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Return one stable run's current status."""

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Cancel one exact mission and run."""


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Explicit private listener admission and lifecycle bounds."""

    host: str
    bearer: str = field(repr=False)
    startup_timeout_seconds: float
    shutdown_timeout_seconds: float

    def __post_init__(self) -> None:
        """Refuse ambiguous admission values or unbounded lifecycle waits."""
        valid = (
            _HOST.fullmatch(self.host) is not None
            and _BEARER.fullmatch(self.bearer) is not None
            and self.startup_timeout_seconds > 0
            and self.shutdown_timeout_seconds > 0
        )
        if not valid:
            message = "invalid private fleet-control server settings"
            raise ValueError(message)


@dataclass(slots=True)
class _Lifecycle:
    accepting: bool = False


def _single_header(request: Request, name: bytes) -> str | None:
    """Return one ASCII header value and reject missing or duplicate values."""
    headers = cast("Sequence[tuple[bytes, bytes]]", request.scope["headers"])
    values = [value for key, value in headers if key.lower() == name]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _identity_refusal(request: Request, settings: ServerSettings) -> ControlRefusal | None:
    """Authenticate Host before bearer, in the governed refusal order."""
    host = _single_header(request, b"host")
    if host is None or not hmac.compare_digest(host, settings.host):
        return ControlRefusal.HOST_INVALID
    authorization = _single_header(request, b"authorization")
    if authorization is None or not hmac.compare_digest(
        authorization,
        f"Bearer {settings.bearer}",
    ):
        return ControlRefusal.AUTHENTICATION_FAILED
    return None


async def _body(request: Request) -> bytes:
    """Read at most the wire-document bound before canonical decoding."""
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAXIMUM_WIRE_BYTES - len(body):
            raise ControlError(ControlRefusal.BODY_TOO_LARGE)
        body.extend(chunk)
    return bytes(body)


async def _parse(
    request: Request,
    settings: ServerSettings,
    model: type[FleetControlStartRequest] | type[FleetControlCancelRequest],
) -> FleetControlStartRequest | FleetControlCancelRequest:
    """Admit identity and media before canonical and schema validation."""
    refused = _identity_refusal(request, settings)
    if refused is not None:
        raise ControlError(refused)
    if _single_header(request, b"content-type") != _JSON:
        raise ControlError(ControlRefusal.UNSUPPORTED_MEDIA_TYPE)
    try:
        value = canonical.decode(await _body(request))
    except canonical.CanonicalizationError as error:
        raise ControlError(ControlRefusal.CANONICAL_JSON_INVALID) from error
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ControlError(ControlRefusal.SCHEMA_INVALID) from error


def _document(model: FleetControlRunStatus, status: int) -> Response:
    """Render one success as canonical JSON."""
    return Response(
        content=canonical.canonical_bytes(model.model_dump(mode="json", by_alias=True)),
        status_code=status,
        media_type=_JSON,
    )


def _refusal(refusal: ControlRefusal) -> Response:
    """Render one closed redacted refusal document."""
    model = FleetControlRefusal.model_validate(
        {"controlVersion": 1, "errorCode": refusal.value, "message": _MESSAGE[refusal]}
    )
    return Response(
        content=canonical.canonical_bytes(model.model_dump(mode="json", by_alias=True)),
        status_code=_STATUS[refusal],
        media_type=_JSON,
    )


async def _invoke(operation: Awaitable[FleetControlRunStatus], status: int) -> Response:
    """Invoke one typed operation and contain all diagnostic detail."""
    try:
        result = await operation
    except ControlError as error:
        return _refusal(error.refusal)
    except Exception:
        return _refusal(ControlRefusal.INTERNAL_FAILURE)
    return _document(result, status)


class _Handlers:
    """Bound route and lifecycle handlers for one application epoch."""

    def __init__(
        self,
        settings: ServerSettings,
        control: FleetControl,
        lifecycle: _Lifecycle,
    ) -> None:
        """Bind admission, use cases, and lifecycle."""
        self.settings = settings
        self.control = control
        self.lifecycle = lifecycle

    @asynccontextmanager
    async def lifespan(self, _application: FastAPI) -> AsyncIterator[None]:
        """Bound startup and shutdown without import-time effects."""
        async with asyncio.timeout(self.settings.startup_timeout_seconds):
            await self.control.startup()
        self.lifecycle.accepting = True
        try:
            yield
        finally:
            self.lifecycle.accepting = False
            async with asyncio.timeout(self.settings.shutdown_timeout_seconds):
                await self.control.shutdown()

    async def health(self, request: Request) -> Response:
        """Report process liveness separately from dependencies."""
        host = _single_header(request, b"host")
        if host is None or not hmac.compare_digest(host, self.settings.host):
            return _refusal(ControlRefusal.HOST_INVALID)
        return Response(content=canonical.canonical_bytes({"status": "live"}), media_type=_JSON)

    async def readiness(self, request: Request) -> Response:
        """Report ready only while accepting and all dependencies are recovered."""
        host = _single_header(request, b"host")
        if host is None or not hmac.compare_digest(host, self.settings.host):
            return _refusal(ControlRefusal.HOST_INVALID)
        ready = self.lifecycle.accepting and self.control.ready
        return Response(
            content=canonical.canonical_bytes({"ready": ready}),
            status_code=200 if ready else 503,
            media_type=_JSON,
        )

    async def start(self, request: Request) -> Response:
        """Validate and start one stable run."""
        try:
            parsed = await _parse(request, self.settings, FleetControlStartRequest)
        except ControlError as error:
            return _refusal(error.refusal)
        if not isinstance(parsed, FleetControlStartRequest):
            return _refusal(ControlRefusal.INTERNAL_FAILURE)
        return await _invoke(self.control.start(parsed), 202)

    async def status(self, request: Request, run_id: str) -> Response:
        """Authenticate and read one identifier-formed run."""
        refused = _identity_refusal(request, self.settings)
        if refused is not None:
            return _refusal(refused)
        if _IDENTIFIER.fullmatch(run_id) is None:
            return _refusal(ControlRefusal.SCHEMA_INVALID)
        return await _invoke(self.control.status(run_id), 200)

    async def cancel(self, request: Request, run_id: str) -> Response:
        """Bind one cancellation body to its path before invoking control."""
        try:
            parsed = await _parse(request, self.settings, FleetControlCancelRequest)
        except ControlError as error:
            return _refusal(error.refusal)
        if not isinstance(parsed, FleetControlCancelRequest):
            return _refusal(ControlRefusal.INTERNAL_FAILURE)
        if parsed.run_id != run_id:
            return _refusal(ControlRefusal.PATH_BODY_MISMATCH)
        return await _invoke(self.control.cancel(parsed), 200)


def create_application(settings: ServerSettings, control: FleetControl) -> FastAPI:
    """Construct the side-effect-free private fleet-control application."""
    lifecycle = _Lifecycle()
    handlers = _Handlers(settings, control, lifecycle)
    application = FastAPI(
        title="Aerial Rescue Fleet Control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=handlers.lifespan,
    )
    application.add_api_route("/healthz", handlers.health, methods=["GET"])
    application.add_api_route("/readyz", handlers.readiness, methods=["GET"])
    application.add_api_route("/internal/v1/runs", handlers.start, methods=["POST"])
    application.add_api_route("/internal/v1/runs/{run_id}", handlers.status, methods=["GET"])
    application.add_api_route(
        "/internal/v1/runs/{run_id}/cancel",
        handlers.cancel,
        methods=["POST"],
    )
    return application
