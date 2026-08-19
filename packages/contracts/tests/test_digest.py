"""The domain-separated digest computed over canonical bytes.

The hash material is asserted independently of the implementation's assembly: each test
builds the expected bytes from the contract in docs/CONTRACTS.md and hashes them here,
so a change to how the digest is assembled fails rather than moving the expectation.
"""

from __future__ import annotations

import hashlib
import unittest

import pytest
from aerial_rescue_contracts import digest

VERSIONED = {"canonicalizationVersion": 1}


def _expected(context: str, canonical: bytes) -> str:
    """Return the digest the contract requires for one context and canonical form."""
    material = b"aerial-rescue/canonical/v1\n" + context.encode() + b"\n" + canonical
    return hashlib.sha256(material).hexdigest()


class DigestMaterialTests(unittest.TestCase):
    def test_the_digest_is_sha256_over_the_domain_separated_canonical_bytes(self) -> None:
        # Arrange
        payload = {"canonicalizationVersion": 1, "missionId": "m1"}

        # Act
        computed = digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertEqual(
            _expected("proposal-digest", b'{"canonicalizationVersion":1,"missionId":"m1"}'),
            computed,
        )

    def test_a_digest_is_sixty_four_lowercase_hexadecimal_characters(self) -> None:
        # Arrange
        payload = dict(VERSIONED)

        # Act
        computed = digest.digest(digest.Context.EVIDENCE, payload)

        # Assert
        self.assertRegex(computed, "^[0-9a-f]{64}$")

    def test_each_context_separates_identical_payloads(self) -> None:
        # Arrange
        payload = dict(VERSIONED)

        # Act
        computed = tuple(digest.digest(context, payload) for context in digest.Context)

        # Assert
        self.assertEqual(len(digest.Context), len(set(computed)))


class DigestFieldExclusionTests(unittest.TestCase):
    def test_a_top_level_digest_member_is_excluded_from_its_own_digest(self) -> None:
        # Arrange
        without = {"canonicalizationVersion": 1, "missionId": "m1"}
        with_digest = {"canonicalizationVersion": 1, "missionId": "m1", "digest": "whatever"}

        # Act
        computed = digest.digest(digest.Context.PROPOSAL, with_digest)

        # Assert
        self.assertEqual(digest.digest(digest.Context.PROPOSAL, without), computed)

    def test_a_nested_digest_member_is_ordinary_data(self) -> None:
        # Arrange
        payload = {"canonicalizationVersion": 1, "inner": {"digest": "kept"}}
        empty_inner = {"canonicalizationVersion": 1, "inner": {}}

        # Act
        computed = digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertNotEqual(digest.digest(digest.Context.PROPOSAL, empty_inner), computed)


class DigestVersionTests(unittest.TestCase):
    def test_a_payload_without_the_canonicalization_version_is_refused(self) -> None:
        # Arrange
        payload = {"missionId": "m1"}

        # Act
        with pytest.raises(digest.DigestError) as captured:
            digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertEqual(digest.DigestRefusal.VERSION, captured.value.refusal)

    def test_a_payload_at_another_canonicalization_version_is_refused(self) -> None:
        # Arrange
        payload = {"canonicalizationVersion": 2}

        # Act
        with pytest.raises(digest.DigestError) as captured:
            digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertEqual(2, captured.value.value)

    def test_a_boolean_version_does_not_pass_as_the_integer_one(self) -> None:
        # Arrange
        payload = {"canonicalizationVersion": True}

        # Act
        with pytest.raises(digest.DigestError) as captured:
            digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertEqual(digest.DigestRefusal.VERSION, captured.value.refusal)

    def test_a_payload_that_is_not_an_object_is_refused(self) -> None:
        # Arrange
        payload = [1, 2, 3]

        # Act
        with pytest.raises(digest.DigestError) as captured:
            digest.digest(digest.Context.PROPOSAL, payload)

        # Assert
        self.assertEqual(digest.DigestRefusal.NOT_AN_OBJECT, captured.value.refusal)
        self.assertEqual(payload, captured.value.value)


class DigestComparisonTests(unittest.TestCase):
    def test_equal_digests_match(self) -> None:
        # Arrange
        computed = digest.digest(digest.Context.PROPOSAL, dict(VERSIONED))

        # Act
        matched = digest.matches(computed, computed)

        # Assert
        self.assertTrue(matched)

    def test_different_digests_do_not_match(self) -> None:
        # Arrange
        computed = digest.digest(digest.Context.PROPOSAL, dict(VERSIONED))
        other = digest.digest(digest.Context.EVIDENCE, dict(VERSIONED))

        # Act
        matched = digest.matches(computed, other)

        # Assert
        self.assertFalse(matched)


class DigestErrorReportingTests(unittest.TestCase):
    def test_the_message_names_both_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = digest.DigestError(digest.DigestRefusal.VERSION, 2)

        # Act
        message = str(error)

        # Assert
        self.assertEqual("digest payload does not declare the canonicalization version: 2", message)


if __name__ == "__main__":
    unittest.main()
