"""Domain-separated digests over canonical bytes.

An approval binds a proposal digest, so two components that hash differently would break
the approval gate silently rather than loudly
(docs/adr/0006-proposal-bound-single-use-approvals.md). The rules here are normative in
docs/CONTRACTS.md and decided by docs/adr/0027-integer-only-canonical-serialization.md.

Like :mod:`aerial_rescue_contracts.canonical`, this module is pure: no input or output,
no clock, and no random source.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import Envelope, envelope_document

CANONICALIZATION_VERSION: Final = 1
"""The version a digest-covered payload must declare inside the hashed bytes."""

VERSION_FIELD: Final = "canonicalizationVersion"
DIGEST_FIELD: Final = "digest"
PROPOSAL_DIGEST_FIELD: Final = "proposalDigest"
EVIDENCE_DECISION_DIGEST_FIELD: Final = "evidenceDecisionDigest"

_PREFIX: Final = b"aerial-rescue/canonical/v1"
_SEPARATOR: Final = b"\n"


class Context(Enum):
    """What a digest is for.

    The context is inside the hashed material, so bytes valid for one purpose cannot be
    presented as another.
    """

    PROPOSAL = "proposal-digest"
    SOURCE_EVENT = "source-event"
    REPLAY_STATE = "replay-state"
    ORDERED_DASHBOARD_EVENT = "ordered-dashboard-event"
    EVIDENCE = "evidence"
    IDEMPOTENCY_BODY = "idempotency-body"


class DigestRefusal(Enum):
    """Why a payload cannot be digested."""

    NOT_AN_OBJECT = "digest payload is not an object"
    VERSION = "digest payload does not declare the canonicalization version"


class DigestError(ValueError):
    """A payload that cannot be digested, carrying the refusal as structured data."""

    def __init__(self, refusal: DigestRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


def digest(context: Context, payload: object) -> str:
    """Return the digest of a payload for one consuming context.

    Args:
        context: What the digest will be compared against.
        payload: An object carrying ``canonicalizationVersion``. A top-level ``digest``
            member is excluded from its own digest; a nested one is ordinary data.

    Returns:
        SHA-256 of the domain-separated canonical bytes, as lowercase hexadecimal.

    Raises:
        DigestError: If the payload is not an object or does not declare the version.
        CanonicalizationError: If any value falls outside the canonical profile.
    """
    covered = _covered_members(payload, DIGEST_FIELD)
    return _digest_covered(context, covered)


def proposal_digest(payload: Mapping[str, object]) -> str:
    """Return ADR-0148's digest of one accepted canonical proposal payload.

    Exactly the top-level ``proposalDigest`` member is omitted. In particular, this
    typed helper does not inherit the generic ``digest`` member exclusion.
    """
    covered = _covered_members(payload, PROPOSAL_DIGEST_FIELD)
    return _digest_covered(Context.PROPOSAL, covered)


def source_event_digest(envelope: Envelope) -> str:
    """Bind a proposal to the complete accepted source-event envelope.

    The wire envelope has its own ``specversion`` rather than a digest canonicalization
    member. Wrapping its reconstructed canonical document puts
    ``canonicalizationVersion`` inside the covered bytes without changing the CloudEvents
    profile or omitting any event member.
    """
    covered: Mapping[object, object] = {
        VERSION_FIELD: CANONICALIZATION_VERSION,
        "event": envelope_document(envelope),
    }
    return _digest_covered(Context.SOURCE_EVENT, covered)


def proposal_digest_matches(payload: Mapping[str, object]) -> bool:
    """Verify an accepted proposal's supplied self-integrity digest."""
    supplied = payload.get(PROPOSAL_DIGEST_FIELD)
    computed = proposal_digest(payload)
    return isinstance(supplied, str) and matches(supplied, computed)


def evidence_decision_digest(payload: Mapping[str, object]) -> str:
    """Return ADR-0148's digest of one accepted evidence-decision payload.

    Exactly the top-level ``evidenceDecisionDigest`` member is omitted.
    """
    covered = _covered_members(payload, EVIDENCE_DECISION_DIGEST_FIELD)
    return _digest_covered(Context.EVIDENCE, covered)


def evidence_decision_digest_matches(payload: Mapping[str, object]) -> bool:
    """Verify an accepted evidence decision's supplied self-integrity digest."""
    supplied = payload.get(EVIDENCE_DECISION_DIGEST_FIELD)
    computed = evidence_decision_digest(payload)
    return isinstance(supplied, str) and matches(supplied, computed)


def ordered_dashboard_event_digest(audit_ordinal: int, event: Mapping[str, object]) -> str:
    """Witness one complete normalized event at its durable audit ordinal.

    The wire wrapper remains ``{auditOrdinal, event}``; this helper adds the
    canonicalization version only inside the digest-covered document as required by
    ADR-0112.
    """
    covered: Mapping[object, object] = {
        VERSION_FIELD: CANONICALIZATION_VERSION,
        "auditOrdinal": audit_ordinal,
        "event": event,
    }
    return _digest_covered(Context.ORDERED_DASHBOARD_EVENT, covered)


def _digest_covered(context: Context, covered: Mapping[object, object]) -> str:
    """Hash already selected members in one versioned, separated context."""
    material = _PREFIX + _SEPARATOR + context.value.encode() + _SEPARATOR + canonical_bytes(covered)
    return hashlib.sha256(material).hexdigest()


def matches(expected: str, actual: str) -> bool:
    """Report whether two digests are equal, without leaking position through timing."""
    return hmac.compare_digest(expected, actual)


def _covered_members(payload: object, omitted_field: str) -> dict[object, object]:
    """Return all members except one named top-level self-integrity field."""
    if not isinstance(payload, Mapping):
        raise DigestError(DigestRefusal.NOT_AN_OBJECT, payload)
    version = payload.get(VERSION_FIELD)
    if type(version) is not int or version != CANONICALIZATION_VERSION:
        raise DigestError(DigestRefusal.VERSION, version)
    return {key: item for key, item in payload.items() if key != omitted_field}
