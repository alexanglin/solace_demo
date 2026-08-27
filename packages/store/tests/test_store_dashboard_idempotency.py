"""Store-backed dashboard claims retain identity and replay committed responses."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_store.idempotency import ClaimRead, StoredClaim, claim, claim_statement
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
BASE_CLAIM: Final = StoredClaim(
    idempotency_key="dashboard-key-0001",
    kind=IdempotencyKind.DASHBOARD_COMMAND,
    body_digest="ab" * 32,
    mission_id="mission-synthetic-0001",
    claimed_at="2026-08-26T12:00:00.000Z",
)
RESULT: Final = b'{"commandId":"command-0001","version":"command-response/v1"}'


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values the PostgreSQL dialect would bind without connecting."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        return self.row


@dataclass
class _RepeatSession:
    row: Sequence[object]

    async def scalar(self, _statement: ClauseElement, /) -> object:
        return None

    async def execute(self, _statement: ClaimRead, /) -> _Rows:
        return _Rows(self.row)


class DashboardClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_dashboard_kind_is_written_as_its_closed_domain_spelling(self) -> None:
        # Arrange
        requests = tuple(
            replace(BASE_CLAIM, kind=kind)
            for kind in (
                IdempotencyKind.DASHBOARD_START,
                IdempotencyKind.DASHBOARD_RESET,
                IdempotencyKind.DASHBOARD_COMMAND,
                IdempotencyKind.DASHBOARD_DECISION,
            )
        )

        # Act
        values = tuple(_parameters(claim_statement(request))["kind"] for request in requests)

        # Assert
        self.assertEqual(
            ("dashboard start", "dashboard reset", "dashboard command", "dashboard decision"),
            values,
        )

    async def test_a_matching_dashboard_repeat_returns_the_exact_stored_response_bytes(
        self,
    ) -> None:
        # Arrange
        session = _RepeatSession((BASE_CLAIM.kind.value, BASE_CLAIM.body_digest, RESULT))

        # Act
        outcome = await claim(session, BASE_CLAIM)

        # Assert
        self.assertEqual(
            (IdempotencyDecision.RETURN_PRIOR_RESULT, RESULT),
            (outcome.decision, outcome.result),
        )


if __name__ == "__main__":
    unittest.main()
