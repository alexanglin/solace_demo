"""HTTP reachability constraints for the closed dashboard refusal vocabulary."""

from __future__ import annotations

import unittest

from aerial_rescue_dashboard_api.boundary.errors import ErrorCode


class PublicErrorReachabilityTests(unittest.TestCase):
    def test_public_vocabulary_excludes_codes_with_no_runtime_producer(self) -> None:
        # Arrange
        producerless = {"STALE_RUNTIME"}

        # Act
        public_codes = {code.value for code in ErrorCode}

        # Assert
        self.assertTrue(producerless.isdisjoint(public_codes))
        self.assertIn("NOT_READY", public_codes)
