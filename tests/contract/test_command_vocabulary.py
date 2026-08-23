"""The wire words a command result may carry, against the state names they come from.

``docs/adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md`` decides that a
result's ``outcome`` is three of the six command-dispatch state names of ADR-0074 -- the three
a drone can cause -- and that the schema and the domain cannot both own that fact.
``packages/contracts`` must not import ``packages/domain``, so this is where the two are held
to each other: the schema is read from disk and compared with the enum, and a rename on either
side fails here rather than producing a wire word nothing maps.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final, cast

from aerial_rescue_domain.commands import CommandState

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
RESULT_SCHEMA: Final = REPOSITORY_ROOT / "schemas/v1/payload/drone-command-result.schema.json"

DRONE_CAUSABLE: Final = (
    CommandState.ACKNOWLEDGED,
    CommandState.SUCCEEDED,
    CommandState.FAILED,
)
"""The states a drone's own report can put a command into, in the order it can cause them."""

GATEWAY_ONLY: Final = (
    CommandState.ACCEPTED,
    CommandState.IN_FLIGHT,
    CommandState.ABANDONED,
)
"""Persisted-not-sent, sent-not-answered, and the gateway's verdict on one it stopped sending."""


def _outcome_values() -> list[str]:
    """Return the ``outcome`` enumeration the committed result schema declares."""
    document = cast("dict[str, object]", json.loads(RESULT_SCHEMA.read_text(encoding="utf-8")))
    properties = cast("dict[str, object]", document["properties"])
    outcome = cast("dict[str, object]", properties["outcome"])
    return cast("list[str]", outcome["enum"])


class CommandResultVocabularyTests(unittest.TestCase):
    def test_the_wire_words_are_the_state_names_a_drone_can_cause(self) -> None:
        # Arrange
        expected = [state.value for state in DRONE_CAUSABLE]

        # Act
        declared = _outcome_values()

        # Assert
        self.assertEqual(expected, declared)

    def test_no_gateway_only_state_can_be_claimed_by_a_drone(self) -> None:
        """`ABANDONED` above all: a drone reporting it would claim the gateway's verdict."""
        # Arrange
        forbidden = frozenset(state.value for state in GATEWAY_ONLY)

        # Act
        claimed = forbidden & frozenset(_outcome_values())

        # Assert
        self.assertEqual(frozenset(), claimed)

    def test_the_two_halves_partition_the_dispatch_states(self) -> None:
        """Every state belongs to exactly one side, so neither list can silently drift."""
        # Arrange
        expected = frozenset(CommandState)

        # Act
        partitioned = frozenset(DRONE_CAUSABLE) | frozenset(GATEWAY_ONLY)

        # Assert
        self.assertEqual(
            (expected, len(DRONE_CAUSABLE) + len(GATEWAY_ONLY)),
            (partitioned, len(expected)),
        )


if __name__ == "__main__":
    unittest.main()
