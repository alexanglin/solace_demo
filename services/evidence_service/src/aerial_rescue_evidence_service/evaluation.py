"""Evidence lifecycle coordination and deterministic score delegation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import Context, digest
from aerial_rescue_domain.evidence import INITIAL_STATE, EvidenceEvent, EvidenceState, transition
from aerial_rescue_domain.scoring import (
    SCORE_VERSION,
    BandBoundaries,
    Contribution,
    EvidenceBand,
    ObservationOrigin,
    ScoreError,
    ScoreRefusal,
    decision_band,
    score,
)
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceItem

from aerial_rescue_evidence_service.ports import ProvenanceFact

V1_BOUNDARIES: Final = BandBoundaries(weak=25, supported=50, corroborated=75)
LIVE_SENSOR_WEIGHT: Final = 40
LIVE_MODEL_WEIGHT: Final = 35


class EvidenceRejectionReason(Enum):
    """The closed evidence rejection reasons this processing path can produce."""

    PROVENANCE_MISSING = "provenance-missing"
    PROVENANCE_MISMATCH = "provenance-mismatch"
    RECORDED_ORIGIN = "recorded-origin"


@dataclass(frozen=True)
class Contributor:
    """One contract-ready contributing source with a service-derived weight."""

    evidence_item_id: str
    source_id: str
    origin: ObservationOrigin
    weight: int
    provenance_digest: str


@dataclass(frozen=True)
class Evaluation:
    """A durable decision branch and the evidence items that led to it."""

    outcome: EvidenceDecisionOutcome
    reason: EvidenceRejectionReason | None
    score: int | None
    band: EvidenceBand | None
    score_version: int | None
    contributors: tuple[Contributor, ...]
    items: tuple[StoredEvidenceItem, ...]


def evaluate(
    mission_id: str,
    proposal_id: str,
    facts: tuple[ProvenanceFact, ...],
) -> Evaluation:
    """Apply the domain lifecycle and score without trusting caller-supplied weights."""
    items = tuple(_stored_item(mission_id, proposal_id, fact) for fact in facts)
    contributions = tuple(_contribution(fact) for fact in facts)
    try:
        band = decision_band(contributions, V1_BOUNDARIES)
    except ScoreError as error:
        if error.refusal is ScoreRefusal.RECORDED_CONTRIBUTION:
            return refused(EvidenceRejectionReason.RECORDED_ORIGIN, items)
        raise
    contributors = tuple(_contributor(fact) for fact in facts)
    return Evaluation(
        outcome=EvidenceDecisionOutcome.CONTRIBUTING,
        reason=None,
        score=score(contributions),
        band=band,
        score_version=SCORE_VERSION,
        contributors=contributors,
        items=items,
    )


def refused(
    reason: EvidenceRejectionReason,
    items: tuple[StoredEvidenceItem, ...] = (),
) -> Evaluation:
    """Return a closed non-contributing decision branch."""
    return Evaluation(
        outcome=EvidenceDecisionOutcome.REJECTED,
        reason=reason,
        score=None,
        band=None,
        score_version=None,
        contributors=(),
        items=items,
    )


def _weight(origin: ObservationOrigin) -> int:
    """Derive the two accepted live weights and a harmless value for domain refusal."""
    if origin is ObservationOrigin.LIVE_SENSOR:
        return LIVE_SENSOR_WEIGHT
    if origin is ObservationOrigin.LIVE_MODEL:
        return LIVE_MODEL_WEIGHT
    return 0


def _contribution(fact: ProvenanceFact) -> Contribution:
    """Build one domain contribution from service-controlled provenance."""
    return Contribution(fact.source_id, fact.origin, _weight(fact.origin))


def _lifecycle(origin: ObservationOrigin) -> EvidenceState:
    """Derive rather than accept the terminal state for an observed source."""
    observed = transition(INITIAL_STATE, EvidenceEvent.OBSERVE)
    if origin is ObservationOrigin.RECORDED:
        return transition(observed, EvidenceEvent.REJECT)
    validated = transition(observed, EvidenceEvent.VALIDATE)
    return transition(validated, EvidenceEvent.ADMIT)


ITEM_IDENTITY_PREFIX: Final = "item-"
"""The prefix of a durable evidence item's identity; the rest is a truncated digest."""

ITEM_IDENTITY_DIGEST_LENGTH: Final = 59
"""Hex digits kept after the prefix, so the identity fits the store's 64-character column."""


def _item_identity(proposal_id: str, fact: ProvenanceFact) -> str:
    """Return the durable identity of one fact's evidence item for one proposal.

    The store keeps one item row per proposal (``proposal_id`` is a member and the reader
    selects by it), while the fact's ``evidence_item_id`` names the observation itself and
    is what the decision publishes. Deriving the row identity from both lets several
    proposals draw on one source fact; the first live run found two proposals for one
    salient event colliding on the fact's identity.
    """
    document = {
        "canonicalizationVersion": 1,
        "proposalId": proposal_id,
        "evidenceItemId": fact.evidence_item_id,
    }
    return ITEM_IDENTITY_PREFIX + digest(Context.EVIDENCE, document)[:ITEM_IDENTITY_DIGEST_LENGTH]


def _stored_item(
    mission_id: str,
    proposal_id: str,
    fact: ProvenanceFact,
) -> StoredEvidenceItem:
    """Map one verified provenance document into the durable evidence row."""
    return StoredEvidenceItem(
        evidence_id=_item_identity(proposal_id, fact),
        mission_id=mission_id,
        proposal_id=proposal_id,
        source_id=fact.source_id,
        source_kind=fact.origin,
        lifecycle=_lifecycle(fact.origin),
        provenance_digest=fact.provenance_digest,
        payload=canonical.canonical_bytes(fact.document),
        observed_at=fact.observed_at,
    )


def _contributor(fact: ProvenanceFact) -> Contributor:
    """Map one live fact into the closed evidence-decision contributor shape."""
    return Contributor(
        evidence_item_id=fact.evidence_item_id,
        source_id=fact.source_id,
        origin=fact.origin,
        weight=_weight(fact.origin),
        provenance_digest=fact.provenance_digest,
    )
