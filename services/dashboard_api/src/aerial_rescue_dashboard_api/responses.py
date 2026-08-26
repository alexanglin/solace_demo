"""Exact canonical JSON responses with route-appropriate cache control."""

from __future__ import annotations

from typing import Final

from aerial_rescue_contracts import canonical
from starlette.responses import Response

from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode

NO_STORE: Final = "no-store"
IMMUTABLE: Final = "public, max-age=31536000, immutable"


def exact_json(body: bytes, status: int = 200) -> Response:
    """Return exact caller-owned JSON bytes with no caching."""
    return Response(
        body,
        status_code=status,
        media_type="application/json",
        headers={"Cache-Control": NO_STORE},
    )


def json_document(document: object, status: int = 200) -> Response:
    """Canonicalize one owned response document exactly once."""
    return exact_json(canonical.canonical_bytes(document), status)


def error_response(error: ApiError | ErrorCode) -> Response:
    """Return the closed redacted error schema for one expected refusal."""
    refusal = error if isinstance(error, ApiError) else ApiError(error)
    return json_document(
        {
            "errorCode": refusal.code.value,
            "errorVersion": "dashboard-error/v1",
            "message": refusal.public_message,
        },
        refusal.status,
    )
