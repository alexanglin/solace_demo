"""Commit Agent Response normalization as the direct path's durable boundary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.integration import agent_response_document
from aerial_rescue_contracts.topics import AGENT_NAME_PATTERN, IDENTIFIER_PATTERN
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity
from aerial_rescue_store.pending_invocations import PendingInvocationError

from aerial_rescue_command_gateway.ingress import AgentResponseIngress, accept_ingress
from aerial_rescue_command_gateway.normalization import (
    NormalizationError,
    NormalizationRefusal,
    NormalizationStamp,
    PendingInvocation,
    build_normalization,
)
from aerial_rescue_command_gateway.ports import DirectDelivery, NormalizationUnitOfWork

CONSUMER: Final = "command-gateway"
SOURCE: Final = "event-mesh-gateway"

INVOCATION_ID_PROPERTY: Final = "aerial-rescue-agent-response-invocation-id"
CORRELATION_ID_PROPERTY: Final = "aerial-rescue-agent-response-correlation-id"
MISSION_ID_PROPERTY: Final = "aerial-rescue-agent-response-mission-id"
SOURCE_EVENT_ID_PROPERTY: Final = "aerial-rescue-agent-response-source-event-id"
SOURCE_EVENT_DIGEST_PROPERTY: Final = "aerial-rescue-agent-response-source-event-digest"
AGENT_NAME_PROPERTY: Final = "aerial-rescue-agent-response-agent-name"
_TRANSPORT_PROPERTIES: Final = frozenset(
    {
        INVOCATION_ID_PROPERTY,
        CORRELATION_ID_PROPERTY,
        MISSION_ID_PROPERTY,
        SOURCE_EVENT_ID_PROPERTY,
        SOURCE_EVENT_DIGEST_PROPERTY,
        AGENT_NAME_PROPERTY,
    }
)
_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"


class NormalizationOutcome(Enum):
    """Whether this response committed new effects or reused an exact prior result."""

    COMMITTED = "committed"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class NormalizationResult:
    """The exact durable result associated with one integration response."""

    outcome: NormalizationOutcome
    result: bytes


def _property(
    properties: Mapping[str, object],
    name: str,
    pattern: str,
) -> str:
    """Return one exact string property or refuse the complete transport context."""
    value = properties.get(name)
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise NormalizationError(NormalizationRefusal.TRANSPORT_CONTEXT)
    return value


def _pending_context(properties: Mapping[str, object]) -> PendingInvocation:
    """Validate the closed gateway-owned property set into durable context."""
    if frozenset(properties) != _TRANSPORT_PROPERTIES:
        raise NormalizationError(NormalizationRefusal.TRANSPORT_CONTEXT)
    return PendingInvocation(
        mission_id=_property(properties, MISSION_ID_PROPERTY, IDENTIFIER_PATTERN),
        agent_name=_property(properties, AGENT_NAME_PROPERTY, AGENT_NAME_PATTERN),
        invocation_id=_property(properties, INVOCATION_ID_PROPERTY, IDENTIFIER_PATTERN),
        correlation_id=_property(properties, CORRELATION_ID_PROPERTY, IDENTIFIER_PATTERN),
        source_event_id=_property(properties, SOURCE_EVENT_ID_PROPERTY, IDENTIFIER_PATTERN),
        source_event_digest=_property(
            properties,
            SOURCE_EVENT_DIGEST_PROPERTY,
            _DIGEST_PATTERN,
        ),
    )


async def handle_agent_response(
    delivery: DirectDelivery,
    stamp: NormalizationStamp,
    unit_of_work: NormalizationUnitOfWork,
) -> NormalizationResult:
    """Normalize one direct response, with no settlement operation to overclaim."""
    ingress = accept_ingress(delivery.payload, delivery.topic)
    if not isinstance(ingress, AgentResponseIngress):
        raise NormalizationError(NormalizationRefusal.RESPONSE_KIND)
    trusted_context = _pending_context(delivery.properties)
    response_bytes = canonical.canonical_bytes(agent_response_document(ingress.response))
    identity = InboxIdentity(
        consumer=CONSUMER,
        source=SOURCE,
        event_id=ingress.response.invocation_id,
        mission_id=ingress.response.mission_id,
        canonical_digest=hashlib.sha256(response_bytes).hexdigest(),
    )
    try:
        async with unit_of_work.begin() as transaction:
            await transaction.record_pending(trusted_context)
            pending = await transaction.load_pending(trusted_context.invocation_id)
            artifacts = build_normalization(ingress, pending, stamp)
            claim = await transaction.claim(identity)
            if claim.decision is InboxDecision.DUPLICATE:
                if claim.result is None:
                    raise NormalizationError(NormalizationRefusal.DUPLICATE_RESULT)
                return NormalizationResult(NormalizationOutcome.DUPLICATE, claim.result)
            if artifacts.proposal is not None:
                await transaction.record_proposal(artifacts.proposal)
            for event in artifacts.events:
                await transaction.stage(event)
            await transaction.complete(identity, artifacts.result, stamp.occurred_at)
    except PendingInvocationError:
        raise NormalizationError(NormalizationRefusal.TRANSPORT_CONTEXT_CONFLICT) from None
    return NormalizationResult(NormalizationOutcome.COMMITTED, artifacts.result)
