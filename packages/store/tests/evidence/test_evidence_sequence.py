"""Durable recovery of the Evidence Service producer sequence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_store.evidence import EvidenceStoreError, EvidenceStoreRefusal, latest_sequence
from aerial_rescue_store.processing.evidence import EvidenceSequenceReader
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class _Session:
    """Return one configured SQL aggregate result."""

    value: object

    async def scalar(self, _statement: object, /) -> object:
        """Return the configured maximum sequence."""
        return self.value

    async def commit(self) -> None:
        """Complete the aggregate read transaction."""

    async def rollback(self) -> None:
        """Permit the shared transaction boundary to roll back."""

    async def close(self) -> None:
        """Release the fake session."""


@pytest.mark.parametrize(("stored", "expected"), [(None, None), (0, 0), (17, 17)])
async def test_latest_sequence_preserves_empty_and_integer_store_results(
    stored: object,
    expected: int | None,
) -> None:
    # Arrange
    session = _Session(stored)

    # Act
    result = await latest_sequence(session)

    # Assert
    assert result == expected


async def test_latest_sequence_refuses_a_non_integer_durable_value() -> None:
    # Arrange
    session = _Session("17")

    # Act
    with pytest.raises(EvidenceStoreError) as captured:
        await latest_sequence(session)

    # Assert
    assert captured.value.refusal is EvidenceStoreRefusal.UNREADABLE_ROW


async def test_sequence_reader_starts_after_the_persisted_decision_and_audit_pair() -> None:
    # Arrange
    session = _Session(None)

    # Act
    with patch(
        "aerial_rescue_store.processing.evidence.latest_sequence",
        AsyncMock(return_value=18),
    ) as latest:
        reader = EvidenceSequenceReader(lambda: cast("AsyncSession", session))
        starting = await reader.starting_sequence()
    latest_args = latest.await_args

    # Assert
    assert latest_args is not None
    assert (starting, latest_args.args[0]) == (20, session)
