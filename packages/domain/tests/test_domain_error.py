"""The structured refusal every domain module raises.

One carrier lets the command gateway audit every denied attempt through one handler and
keeps the message form identical to the contracts package's errors.
"""

from __future__ import annotations

import unittest
from enum import Enum

from aerial_rescue_domain import DomainError


class Why(Enum):
    SOMETHING = "why"


class DomainErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = DomainError(Why.SOMETHING, 7)

        # Act
        message = str(error)

        # Assert
        self.assertEqual(("why: 7", Why.SOMETHING, 7), (message, error.refusal, error.value))

    def test_a_domain_error_is_a_value_error(self) -> None:
        # Arrange
        error = DomainError(Why.SOMETHING, "x")

        # Act
        is_value_error = isinstance(error, ValueError)

        # Assert
        self.assertTrue(is_value_error)


if __name__ == "__main__":
    unittest.main()
