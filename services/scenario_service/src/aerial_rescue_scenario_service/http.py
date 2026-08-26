"""Authenticated FastAPI boundary for the five private scenario-control routes."""

from __future__ import annotations

import hmac
import ipaddress
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

import uvicorn
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.canonical import canonical_bytes
from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from aerial_rescue_scenario_service.control import ScenarioControlError
from aerial_rescue_scenario_service.wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    ScenarioCatalogResponse,
    ScenarioControlCancelRequest,
    ScenarioControlRecoveryRequest,
    ScenarioControlRefusal,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
)

CONTROL_PORT: Final = 8081
_CONTROL_VERSION: Final = 1
_JSON_MEDIA_TYPE: Final = "application/json"
_MINIMUM_SECRET_CHARACTERS: Final = 32

type ScenarioRefusalCode = Literal[
    "HOST_INVALID",
    "AUTHENTICATION_FAILED",
    "UNSUPPORTED_MEDIA_TYPE",
    "BODY_TOO_LARGE",
    "CANONICAL_JSON_INVALID",
    "SCHEMA_INVALID",
    "PATH_BODY_MISMATCH",
    "RUN_CONFLICT",
    "RUN_NOT_FOUND",
    "CANCELLATION_NOT_ESTABLISHED",
    "SCENARIO_NOT_FOUND",
    "SCENARIO_REVISION_MISMATCH",
    "FLEET_UNAVAILABLE",
    "INTERNAL_FAILURE",
]

_HOST_INVALID: Final[ScenarioRefusalCode] = "HOST_INVALID"
_AUTHENTICATION_FAILED: Final[ScenarioRefusalCode] = "AUTHENTICATION_FAILED"
_UNSUPPORTED_MEDIA_TYPE: Final[ScenarioRefusalCode] = "UNSUPPORTED_MEDIA_TYPE"
_BODY_TOO_LARGE: Final[ScenarioRefusalCode] = "BODY_TOO_LARGE"
_CANONICAL_JSON_INVALID: Final[ScenarioRefusalCode] = "CANONICAL_JSON_INVALID"
_SCHEMA_INVALID: Final[ScenarioRefusalCode] = "SCHEMA_INVALID"
_PATH_BODY_MISMATCH: Final[ScenarioRefusalCode] = "PATH_BODY_MISMATCH"
_INTERNAL_FAILURE: Final[ScenarioRefusalCode] = "INTERNAL_FAILURE"

_HTTP_STATUS: Final = {
    "HOST_INVALID": 400,
    "AUTHENTICATION_FAILED": 401,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "BODY_TOO_LARGE": 413,
    "CANONICAL_JSON_INVALID": 400,
    "SCHEMA_INVALID": 422,
    "PATH_BODY_MISMATCH": 409,
    "RUN_CONFLICT": 409,
    "RUN_NOT_FOUND": 404,
    "CANCELLATION_NOT_ESTABLISHED": 409,
    "SCENARIO_NOT_FOUND": 404,
    "SCENARIO_REVISION_MISMATCH": 409,
    "FLEET_UNAVAILABLE": 503,
    "INTERNAL_FAILURE": 500,
}

_MESSAGES: Final = {
    "HOST_INVALID": "request Host is not accepted",
    "AUTHENTICATION_FAILED": "private authentication failed",
    "UNSUPPORTED_MEDIA_TYPE": "request must carry application/json",
    "BODY_TOO_LARGE": "request body exceeds the private bound",
    "CANONICAL_JSON_INVALID": "request is not canonical-profile JSON",
    "SCHEMA_INVALID": "request does not satisfy the private schema",
    "PATH_BODY_MISMATCH": "path and body run identifiers differ",
    "RUN_CONFLICT": "run identity conflicts with accepted content",
    "RUN_NOT_FOUND": "run is not known",
    "CANCELLATION_NOT_ESTABLISHED": "run did not stop inside the cancellation bound",
    "SCENARIO_NOT_FOUND": "scenario is not known",
    "SCENARIO_REVISION_MISMATCH": "scenario revision is not accepted",
    "FLEET_UNAVAILABLE": "fleet control is unavailable",
    "INTERNAL_FAILURE": "scenario control failed internally",
}


class ScenarioOperations(Protocol):
    """The five scenario operations behind private HTTP admission."""

    def catalog_response(self) -> ScenarioCatalogResponse:
        """Return the validated browser-facing catalog."""

    def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        """Start or return one stable scenario run."""

    def status(self, run_id: str) -> ScenarioControlRunStatus:
        """Return one scenario run's current status."""

    def cancel(self, run_id: str, mission_id: str) -> ScenarioControlRunStatus:
        """Stop one exact mission run."""

    def recover(self, request: ScenarioControlRecoveryRequest) -> ScenarioControlRunStatus:
        """Reconcile one durable run that the fleet may have lost."""


@dataclass(frozen=True, repr=False)
class ScenarioHttpConfig:
    """One exact Host and one hop-specific private bearer."""

    expected_host: str
    bearer_secret: str
    maximum_body_bytes: int = MAX_WIRE_DOCUMENT_BYTES

    def __post_init__(self) -> None:
        """Refuse ambiguous Host, weak bearer forms, and unbounded request bodies."""
        if not self.expected_host or not self.expected_host.isascii():
            message = "expected_host must be nonempty ASCII"
            raise ValueError(message)
        if len(self.bearer_secret) < _MINIMUM_SECRET_CHARACTERS or not self.bearer_secret.isascii():
            message = "bearer_secret must be at least 256 ASCII bits"
            raise ValueError(message)
        if self.maximum_body_bytes < 1:
            message = "maximum_body_bytes must be positive"
            raise ValueError(message)


class _AdmissionError(ValueError):
    """One ordered HTTP admission refusal."""

    def __init__(self, code: ScenarioRefusalCode) -> None:
        super().__init__(code)
        self.code = code


def _headers(request: Request, name: bytes) -> tuple[bytes, ...]:
    return tuple(value for key, value in request.scope["headers"] if key.lower() == name)


def _single_ascii_header(request: Request, name: bytes, refusal: ScenarioRefusalCode) -> str:
    values = _headers(request, name)
    if len(values) != 1:
        raise _AdmissionError(refusal)
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise _AdmissionError(refusal) from error


def _admit_host(request: Request, config: ScenarioHttpConfig) -> None:
    host = _single_ascii_header(request, b"host", _HOST_INVALID)
    if not hmac.compare_digest(host, config.expected_host):
        raise _AdmissionError(_HOST_INVALID)


def _admit_bearer(request: Request, config: ScenarioHttpConfig) -> None:
    authorization = _single_ascii_header(request, b"authorization", _AUTHENTICATION_FAILED)
    if not hmac.compare_digest(authorization, f"Bearer {config.bearer_secret}"):
        raise _AdmissionError(_AUTHENTICATION_FAILED)


def _admit_media(request: Request) -> None:
    value = _single_ascii_header(request, b"content-type", _UNSUPPORTED_MEDIA_TYPE)
    parts = tuple(part.strip().lower() for part in value.split(";"))
    if not parts or parts[0] != _JSON_MEDIA_TYPE:
        raise _AdmissionError(_UNSUPPORTED_MEDIA_TYPE)
    if any(part != "charset=utf-8" for part in parts[1:]):
        raise _AdmissionError(_UNSUPPORTED_MEDIA_TYPE)


async def _bounded_body(request: Request, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum_bytes:
            raise _AdmissionError(_BODY_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


async def _body_model[ModelT: BaseModel](
    request: Request,
    config: ScenarioHttpConfig,
    model: type[ModelT],
) -> ModelT:
    _admit_media(request)
    raw = await _bounded_body(request, config.maximum_body_bytes)
    try:
        document = canonical.decode(raw)
    except canonical.CanonicalizationError as error:
        raise _AdmissionError(_CANONICAL_JSON_INVALID) from error
    try:
        return model.model_validate(document)
    except ValidationError as error:
        raise _AdmissionError(_SCHEMA_INVALID) from error


def _json(model: BaseModel, status_code: int) -> Response:
    return Response(
        content=canonical_bytes(model.model_dump(by_alias=True)),
        status_code=status_code,
        media_type=_JSON_MEDIA_TYPE,
        headers={"Cache-Control": "no-store"},
    )


def _refusal(code: ScenarioRefusalCode) -> Response:
    return _json(
        ScenarioControlRefusal(
            controlVersion=_CONTROL_VERSION,
            errorCode=code,
            message=_MESSAGES[code],
        ),
        _HTTP_STATUS[code],
    )


async def _admission_exception(_request: Request, error: Exception) -> Response:
    """Translate an ordered admission refusal without exposing request material."""
    admitted = cast("_AdmissionError", error)
    return _refusal(admitted.code)


async def _operation_exception(_request: Request, error: Exception) -> Response:
    """Translate a scenario operation refusal without exposing its diagnostic value."""
    operation = cast("ScenarioControlError", error)
    return _refusal(operation.code.value)


async def _internal_exception(_request: Request, _error: Exception) -> Response:
    """Translate every unexpected route failure to one redacted refusal."""
    return _refusal(_INTERNAL_FAILURE)


def _bind_run(path_run_id: str, body_run_id: str) -> None:
    """Apply path/body run binding immediately before operation policy."""
    if body_run_id != path_run_id:
        raise _AdmissionError(_PATH_BODY_MISMATCH)


@dataclass(frozen=True)
class _ScenarioHttpBoundary:
    """Route handlers over one injected operation and admission boundary."""

    operations: ScenarioOperations
    config: ScenarioHttpConfig

    async def catalog(self, request: Request) -> Response:
        """Authenticate catalog discovery before consulting the loader."""
        _admit_host(request, self.config)
        _admit_bearer(request, self.config)
        return _json(self.operations.catalog_response(), 200)

    async def start(self, request: Request) -> Response:
        """Admit and execute one scenario start."""
        _admit_host(request, self.config)
        _admit_bearer(request, self.config)
        body = await _body_model(request, self.config, ScenarioControlStartRequest)
        return _json(self.operations.start(body), 202)

    async def status(self, run_id: str, request: Request) -> Response:
        """Authenticate a read before looking up its run."""
        _admit_host(request, self.config)
        _admit_bearer(request, self.config)
        return _json(self.operations.status(run_id), 200)

    async def cancel(self, run_id: str, request: Request) -> Response:
        """Admit, bind, and execute one scenario cancellation."""
        _admit_host(request, self.config)
        _admit_bearer(request, self.config)
        body = await _body_model(request, self.config, ScenarioControlCancelRequest)
        _bind_run(run_id, body.run_id)
        return _json(self.operations.cancel(run_id, body.mission_id), 200)

    async def recover(self, run_id: str, request: Request) -> Response:
        """Admit, bind, and execute one lost-run recovery."""
        _admit_host(request, self.config)
        _admit_bearer(request, self.config)
        body = await _body_model(request, self.config, ScenarioControlRecoveryRequest)
        _bind_run(run_id, body.run_id)
        return _json(self.operations.recover(body), 200)


def create_app(operations: ScenarioOperations, config: ScenarioHttpConfig) -> FastAPI:
    """Create the side-effect-free five-route scenario private application."""
    app = FastAPI(
        title="Aerial Rescue Scenario Control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_exception_handler(_AdmissionError, _admission_exception)
    app.add_exception_handler(ScenarioControlError, _operation_exception)
    app.add_exception_handler(Exception, _internal_exception)
    boundary = _ScenarioHttpBoundary(operations, config)
    app.add_api_route("/internal/v1/scenarios", boundary.catalog, methods=["GET"])
    app.add_api_route("/internal/v1/runs", boundary.start, methods=["POST"])
    app.add_api_route("/internal/v1/runs/{run_id}", boundary.status, methods=["GET"])
    app.add_api_route("/internal/v1/runs/{run_id}/cancel", boundary.cancel, methods=["POST"])
    app.add_api_route("/internal/v1/runs/{run_id}/recover", boundary.recover, methods=["POST"])
    return app


def serve(app: FastAPI) -> None:
    """Run the injected application on the accepted internal listener port."""
    bind_host = str(ipaddress.ip_address(0))
    uvicorn.run(app, host=bind_host, port=CONTROL_PORT, access_log=False)
