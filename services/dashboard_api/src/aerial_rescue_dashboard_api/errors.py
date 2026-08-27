"""Closed, redacted dashboard API refusal vocabulary and HTTP mapping."""

from __future__ import annotations

from enum import Enum
from typing import Final


class ErrorCode(Enum):
    """The complete public dashboard error-code vocabulary."""

    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    CANONICAL_JSON_INVALID = "CANONICAL_JSON_INVALID"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    HOST_INVALID = "HOST_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    ORIGIN_INVALID = "ORIGIN_INVALID"
    REPLAY_SESSION_NOT_FOUND = "REPLAY_SESSION_NOT_FOUND"
    RUN_CONFLICT = "RUN_CONFLICT"
    SCENARIO_NOT_FOUND = "SCENARIO_NOT_FOUND"
    SCENARIO_REVISION_MISMATCH = "SCENARIO_REVISION_MISMATCH"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SSE_CAPACITY_EXCEEDED = "SSE_CAPACITY_EXCEEDED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"


STATUS_BY_CODE: Final = {
    ErrorCode.HOST_INVALID: 400,
    ErrorCode.IDEMPOTENCY_KEY_INVALID: 400,
    ErrorCode.CANONICAL_JSON_INVALID: 400,
    ErrorCode.SCHEMA_INVALID: 400,
    ErrorCode.AUTHENTICATION_FAILED: 401,
    ErrorCode.ORIGIN_INVALID: 403,
    ErrorCode.SCENARIO_NOT_FOUND: 404,
    ErrorCode.REPLAY_SESSION_NOT_FOUND: 404,
    ErrorCode.ASSET_NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.SCENARIO_REVISION_MISMATCH: 409,
    ErrorCode.OPERATION_CONFLICT: 409,
    ErrorCode.RUN_CONFLICT: 409,
    ErrorCode.CANCELLATION_NOT_ESTABLISHED: 409,
    ErrorCode.BODY_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.SSE_CAPACITY_EXCEEDED: 503,
    ErrorCode.INTERNAL_FAILURE: 500,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
}

MESSAGE_BY_CODE: Final = {
    ErrorCode.ASSET_NOT_FOUND: "requested dashboard asset was not found",
    ErrorCode.AUTHENTICATION_FAILED: "dashboard mutation authentication failed",
    ErrorCode.BODY_TOO_LARGE: "dashboard mutation body exceeds the accepted bound",
    ErrorCode.CANCELLATION_NOT_ESTABLISHED: (
        "current run cancellation was not established within the bounded interval"
    ),
    ErrorCode.CANONICAL_JSON_INVALID: "request body is not accepted canonical-profile JSON",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "dashboard dependency is unavailable",
    ErrorCode.HOST_INVALID: "request Host is not accepted",
    ErrorCode.IDEMPOTENCY_CONFLICT: "idempotency key belongs to different request content",
    ErrorCode.IDEMPOTENCY_KEY_INVALID: "Idempotency-Key must be a lowercase UUID version 4",
    ErrorCode.INTERNAL_FAILURE: "dashboard request failed",
    ErrorCode.METHOD_NOT_ALLOWED: "request method is not allowed for this route",
    ErrorCode.OPERATION_CONFLICT: "another dashboard operation prevents this mutation",
    ErrorCode.ORIGIN_INVALID: "request Origin is not accepted",
    ErrorCode.REPLAY_SESSION_NOT_FOUND: "replay session was not found",
    ErrorCode.RUN_CONFLICT: "dashboard run state conflicts with this operation",
    ErrorCode.SCENARIO_NOT_FOUND: "scenario was not found",
    ErrorCode.SCENARIO_REVISION_MISMATCH: "scenario revision is not available",
    ErrorCode.SCHEMA_INVALID: "request does not match the closed dashboard schema",
    ErrorCode.SSE_CAPACITY_EXCEEDED: "dashboard event-stream client capacity is exhausted",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "dashboard mutation requires application/json",
}


class ApiError(Exception):
    """An expected public refusal carrying no secret or internal representation."""

    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        """Create a closed refusal with its stable default status and message."""
        super().__init__(message or MESSAGE_BY_CODE[code])
        self.code = code
        self.status = STATUS_BY_CODE[code]
        self.public_message = message or MESSAGE_BY_CODE[code]
