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

CANONICALIZATION_VERSION: Final = 1
"""The version a digest-covered payload must declare inside the hashed bytes."""

VERSION_FIELD: Final = "canonicalizationVersion"
DIGEST_FIELD: Final = "digest"

_PREFIX: Final = b"aerial-rescue/canonical/v1"
_SEPARATOR: Final = b"\n"


class Context(Enum):
    """What a digest is for.

    The context is inside the hashed material, so bytes valid for one purpose cannot be
    presented as another.
    """

    PROPOSAL = "proposal-digest"
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
    covered = _covered_members(payload)
    material = _PREFIX + _SEPARATOR + context.value.encode() + _SEPARATOR + canonical_bytes(covered)
    return hashlib.sha256(material).hexdigest()


def matches(expected: str, actual: str) -> bool:
    """Report whether two digests are equal, without leaking position through timing."""
    return hmac.compare_digest(expected, actual)


def _covered_members(payload: object) -> dict[object, object]:
    """Return the members a digest covers, refusing a payload the contract excludes."""
    if not isinstance(payload, Mapping):
        raise DigestError(DigestRefusal.NOT_AN_OBJECT, payload)
    version = payload.get(VERSION_FIELD)
    if type(version) is not int or version != CANONICALIZATION_VERSION:
        raise DigestError(DigestRefusal.VERSION, version)
    return {key: item for key, item in payload.items() if key != DIGEST_FIELD}
