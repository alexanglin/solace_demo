"""Trusted proposal normalization as one live-model evidence contribution."""

from __future__ import annotations

from dataclasses import replace

from aerial_rescue_contracts.digest import Context, digest
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from aerial_rescue_evidence_service.processing import handle_delivery

from .support import provenance_fact, source_evidence, stored_proposal
from .test_processing import STAMP, FakeSettlement, FakeTransaction, FakeUnitOfWork, _delivery


async def test_authoritative_proposal_adds_one_model_fact_to_its_sensor_source() -> None:
    # Arrange
    sensor = provenance_fact(
        "evidence-item-sensor-0001",
        "drone-vision-01",
        ObservationOrigin.LIVE_SENSOR,
    )
    document = {**sensor.document, "sourceEventDigest": stored_proposal().source_event_digest}
    sensor = replace(
        sensor,
        document=document,
        provenance_digest=digest(Context.EVIDENCE, document),
    )
    transaction = FakeTransaction(stored_proposal(), source_evidence(sensor))
    unit_of_work = FakeUnitOfWork(transaction)
    settlement = FakeSettlement(transaction.order)

    # Act
    await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

    # Assert
    assert (
        transaction.decisions[0].score,
        transaction.decisions[0].band,
        tuple(item.source_kind for item in transaction.items),
        tuple(item.source_id for item in transaction.items),
        transaction.order[-3:],
    ) == (
        75,
        EvidenceBand.CORROBORATED,
        (ObservationOrigin.LIVE_SENSOR, ObservationOrigin.LIVE_MODEL),
        ("drone-vision-01", "invocation-bound-0001"),
        ["complete", "commit", "settle"],
    )
