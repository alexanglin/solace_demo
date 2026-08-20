"""Warning policy for project-owned Agent Mesh tooling."""

from __future__ import annotations

import unittest
import warnings

import pytest
from pydantic.warnings import PydanticDeprecatedSince20


class WarningPolicyTests(unittest.TestCase):
    def test_owned_pydantic_deprecations_remain_errors(self) -> None:
        # Arrange
        message = "owned Agent Mesh tooling used a deprecated Pydantic feature"

        # Act
        with pytest.raises(PydanticDeprecatedSince20) as raised:
            warnings.warn(message, PydanticDeprecatedSince20, stacklevel=1)

        # Assert
        self.assertIn(message, str(raised.value))


if __name__ == "__main__":
    unittest.main()
