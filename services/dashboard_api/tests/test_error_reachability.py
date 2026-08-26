"""Reachability constraints for the closed public dashboard refusal vocabulary."""

from __future__ import annotations

import unittest

from aerial_rescue_dashboard_api.errors import ErrorCode


class PublicErrorReachabilityTests(unittest.TestCase):
    def test_public_vocabulary_excludes_codes_with_no_runtime_producer(self) -> None:
        # Arrange
        producerless = {"NOT_READY", "STALE_RUNTIME"}

        # Act
        public_codes = {code.value for code in ErrorCode}

        # Assert
        self.assertTrue(producerless.isdisjoint(public_codes))
