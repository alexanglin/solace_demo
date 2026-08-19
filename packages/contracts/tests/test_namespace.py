"""The versioned application event namespace every application topic begins with."""

from __future__ import annotations

import unittest

from aerial_rescue_contracts import namespace_prefix


class NamespacePrefixTests(unittest.TestCase):
    def test_the_prefix_is_the_versioned_application_event_namespace(self) -> None:
        # Arrange
        expected = "aerial-rescue/v1"

        # Act
        prefix = namespace_prefix()

        # Assert
        self.assertEqual(expected, prefix)


if __name__ == "__main__":
    unittest.main()
