"""Body-free malformed Guaranteed-ingress evidence and message-bound rejection."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import Protocol

from aerial_rescue_broker.messaging import UnsettledMessageMetadata
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome

from aerial_rescue_command_gateway.ports import GuaranteedDelivery

CONSUMER = "command-gateway"


class RefusalPersistence(Protocol):
    """Commit one body-free fact through a separate store transaction."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return only after the fact or exact prior observation commits."""


class RejectedSettlement(Protocol):
    """The exact malformed Guaranteed message's permanent settlement capability."""

    async def reject(self) -> None:
        """Move the message through its source queue's isolated DMQ policy."""


def candidate(
    delivery: GuaranteedDelivery,
    channel: str,
    refusal_code: str,
) -> BrokerRefusalCandidate:
    """Derive only safe context and the lowercase SHA-256 of exact arriving bytes."""
    family: str | None = None
    source: str | None = None
    with suppress(ValueError):
        family = parse_topic(delivery.topic).family.literal_suffix
    with suppress(ValueError):
        source = decode_envelope(delivery.payload).source
    return BrokerRefusalCandidate(
        consumer=CONSUMER,
        source=source,
        family=family,
        channel=channel,
        refusal_code=refusal_code,
        raw_digest=hashlib.sha256(delivery.payload).hexdigest(),
    )


def native_trace_candidate(
    metadata: UnsettledMessageMetadata,
    channel: str,
) -> BrokerRefusalCandidate:
    """Bind safe receiver metadata to this consumer without recovering hostile bytes."""
    return BrokerRefusalCandidate(
        consumer=CONSUMER,
        source=metadata.source,
        family=metadata.family,
        channel=channel,
        refusal_code="native-trace-refused",
        raw_digest=metadata.raw_digest,
    )


async def reject_after_refusal(
    delivery: GuaranteedDelivery,
    channel: str,
    refusal_code: str,
    persistence: RefusalPersistence,
    settlement: RejectedSettlement,
) -> None:
    """Commit bounded evidence, then reject only this delivery; failure leaves it unsettled."""
    await persistence.refuse(candidate(delivery, channel, refusal_code))
    await settlement.reject()
