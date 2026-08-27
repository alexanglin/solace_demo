"""Closed broker properties for transport-authenticated Agent Responses."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

INVOCATION_ID_PROPERTY: Final = "aerial-rescue-agent-response-invocation-id"
CORRELATION_ID_PROPERTY: Final = "aerial-rescue-agent-response-correlation-id"
MISSION_ID_PROPERTY: Final = "aerial-rescue-agent-response-mission-id"
SOURCE_EVENT_ID_PROPERTY: Final = "aerial-rescue-agent-response-source-event-id"
SOURCE_EVENT_DIGEST_PROPERTY: Final = "aerial-rescue-agent-response-source-event-digest"
AGENT_NAME_PROPERTY: Final = "aerial-rescue-agent-response-agent-name"

_PROPERTY_NAMES: Final = frozenset(
    {
        INVOCATION_ID_PROPERTY,
        CORRELATION_ID_PROPERTY,
        MISSION_ID_PROPERTY,
        SOURCE_EVENT_ID_PROPERTY,
        SOURCE_EVENT_DIGEST_PROPERTY,
        AGENT_NAME_PROPERTY,
    }
)
_IDENTIFIER: Final = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_AGENT_NAME: Final = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_LOWERCASE_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_BOUND_PROPERTIES: ContextVar[dict[str, str] | None] = ContextVar(
    "aerial_rescue_gateway_transport_properties",
    default=None,
)


class GatewayTransportContextError(ValueError):
    """Trusted context cannot form the exact Agent Response property set."""


def _forwarded(
    context: Mapping[str, object],
    name: str,
    pattern: re.Pattern[str],
) -> str:
    """Return one trusted context string in its exact wire form."""
    value = context.get(name)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise GatewayTransportContextError
    return value


def build_gateway_transport_properties(
    forwarded_context: Mapping[str, object],
    invocation_id: str,
) -> dict[str, str]:
    """Derive the closed property set only from trusted forwarding context."""
    if _IDENTIFIER.fullmatch(invocation_id) is None:
        raise GatewayTransportContextError
    return {
        INVOCATION_ID_PROPERTY: invocation_id,
        CORRELATION_ID_PROPERTY: _forwarded(
            forwarded_context,
            "correlationId",
            _IDENTIFIER,
        ),
        MISSION_ID_PROPERTY: _forwarded(forwarded_context, "missionId", _IDENTIFIER),
        SOURCE_EVENT_ID_PROPERTY: _forwarded(
            forwarded_context,
            "sourceEventId",
            _IDENTIFIER,
        ),
        SOURCE_EVENT_DIGEST_PROPERTY: _forwarded(
            forwarded_context,
            "sourceEventDigest",
            _LOWERCASE_SHA256,
        ),
        AGENT_NAME_PROPERTY: _forwarded(forwarded_context, "agentName", _AGENT_NAME),
    }


def _validate_properties(properties: Mapping[str, object]) -> dict[str, str]:
    """Copy only the exact valid property set admitted to the publisher scope."""
    if frozenset(properties) != _PROPERTY_NAMES:
        raise GatewayTransportContextError
    forwarded = {
        "correlationId": properties[CORRELATION_ID_PROPERTY],
        "missionId": properties[MISSION_ID_PROPERTY],
        "sourceEventId": properties[SOURCE_EVENT_ID_PROPERTY],
        "sourceEventDigest": properties[SOURCE_EVENT_DIGEST_PROPERTY],
        "agentName": properties[AGENT_NAME_PROPERTY],
    }
    invocation_id = properties[INVOCATION_ID_PROPERTY]
    if not isinstance(invocation_id, str):
        raise GatewayTransportContextError
    return build_gateway_transport_properties(forwarded, invocation_id)


@contextmanager
def bind_gateway_transport_properties(
    properties: Mapping[str, object],
) -> Iterator[None]:
    """Bind one immutable property copy to the current asynchronous publication."""
    token = _BOUND_PROPERTIES.set(_validate_properties(properties))
    try:
        yield
    finally:
        _BOUND_PROPERTIES.reset(token)


def current_gateway_transport_properties() -> dict[str, str]:
    """Return a copy of the current bound properties or refuse unbound publication."""
    properties = _BOUND_PROPERTIES.get()
    if properties is None:
        raise GatewayTransportContextError
    return dict(properties)
