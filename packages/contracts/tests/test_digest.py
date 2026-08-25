"""The domain-separated digest computed over canonical bytes.

The hash material is asserted independently of the implementation's assembly: each test
builds the expected bytes from the contract in docs/CONTRACTS.md and hashes them here,
so a change to how the digest is assembled fails rather than moving the expectation.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import digest
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import parse_envelope

VERSIONED = {"canonicalizationVersion": 1}
GOLDEN_ROOT = next(
    parent / "fixtures" / "golden" / "v1"
    for parent in Path(__file__).resolve().parents
    if (parent / "fixtures" / "golden" / "v1").is_dir()
)


def _expected(context: str, canonical: bytes) -> str:
    """Return the digest the contract requires for one context and canonical form."""
    material = b"aerial-rescue/canonical/v1\n" + context.encode() + b"\n" + canonical
    return hashlib.sha256(material).hexdigest()


def _fixture(path: Path) -> dict[str, object]:
    """Load one committed JSON-object fixture."""
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _payload(document: Mapping[str, object]) -> Mapping[str, object]:
    """Return an event's data or a standalone payload document."""
    data = document.get("data")
    return cast("Mapping[str, object]", data) if isinstance(data, Mapping) else document


def _mappings(value: object) -> Iterator[Mapping[str, object]]:
    """Yield every object nested in a fixture."""
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, object]", value)
        yield mapping
        for member in mapping.values():
            yield from _mappings(member)
    elif isinstance(value, list):
        for member in cast("list[object]", value):
            yield from _mappings(member)


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


class ApplicationDocumentDigestTests(unittest.TestCase):
    def test_source_event_digest_covers_the_complete_accepted_event_in_its_own_context(
        self,
    ) -> None:
        # Arrange
        document = _fixture(GOLDEN_ROOT / "event" / "drone-event-salient" / "baseline.json")
        envelope = parse_envelope(document)
        covered = {"canonicalizationVersion": 1, "event": document}

        # Act
        computed = digest.source_event_digest(envelope)

        # Assert
        self.assertEqual(
            _expected("source-event", canonical_bytes(covered)),
            computed,
        )

    def test_source_event_digest_changes_when_envelope_metadata_or_payload_changes(self) -> None:
        # Arrange
        document = _fixture(GOLDEN_ROOT / "event" / "drone-event-salient" / "baseline.json")
        changed_metadata = dict(
            document,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203339-01",
        )
        changed_payload = dict(document)
        changed_payload["data"] = dict(
            cast("Mapping[str, object]", document["data"]), detail="changed"
        )

        # Act
        computed = tuple(
            digest.source_event_digest(parse_envelope(candidate))
            for candidate in (document, changed_metadata, changed_payload)
        )

        # Assert
        self.assertEqual(3, len(set(computed)))

    def test_proposal_digest_omits_exactly_the_proposal_self_integrity_member(self) -> None:
        # Arrange
        payload = {
            "canonicalizationVersion": 1,
            "missionId": "m1",
            "digest": "ordinary-data",
            "proposalDigest": "not-covered",
        }

        # Act
        computed = digest.proposal_digest(payload)

        # Assert
        self.assertEqual(
            _expected(
                "proposal-digest",
                b'{"canonicalizationVersion":1,"digest":"ordinary-data","missionId":"m1"}',
            ),
            computed,
        )

    def test_evidence_decision_digest_uses_its_named_context_and_self_member(self) -> None:
        # Arrange
        payload = {
            "canonicalizationVersion": 1,
            "evidenceDecisionId": "decision-1",
            "evidenceDecisionDigest": "not-covered",
        }

        # Act
        computed = digest.evidence_decision_digest(payload)

        # Assert
        self.assertEqual(
            _expected(
                "evidence",
                b'{"canonicalizationVersion":1,"evidenceDecisionId":"decision-1"}',
            ),
            computed,
        )

    def test_supplied_application_document_digests_verify_in_constant_time(self) -> None:
        # Arrange
        proposal = {
            "canonicalizationVersion": 1,
            "missionId": "m1",
            "sourceEventDigest": "1" * 64,
            "proposalDigest": "",
        }
        proposal["proposalDigest"] = digest.proposal_digest(proposal)
        decision = {
            "canonicalizationVersion": 1,
            "proposalDigest": proposal["proposalDigest"],
            "evidenceDecisionDigest": "",
        }
        decision["evidenceDecisionDigest"] = digest.evidence_decision_digest(decision)

        # Act
        proposal_matches = digest.proposal_digest_matches(proposal)
        evidence_matches = digest.evidence_decision_digest_matches(decision)

        # Assert
        self.assertTrue(proposal_matches)
        self.assertTrue(evidence_matches)

    def test_tampering_with_any_covered_application_member_is_refused(self) -> None:
        # Arrange
        proposal = {
            "canonicalizationVersion": 1,
            "missionId": "m1",
            "sourceEventDigest": "1" * 64,
            "proposalDigest": "",
        }
        proposal["proposalDigest"] = digest.proposal_digest(proposal)
        decision = {
            "canonicalizationVersion": 1,
            "proposalDigest": proposal["proposalDigest"],
            "outcome": "abstained",
            "evidenceDecisionDigest": "",
        }
        decision["evidenceDecisionDigest"] = digest.evidence_decision_digest(decision)
        proposal["sourceEventDigest"] = "2" * 64
        decision["outcome"] = "rejected"

        # Act
        proposal_matches = digest.proposal_digest_matches(proposal)
        evidence_matches = digest.evidence_decision_digest_matches(decision)

        # Assert
        self.assertFalse(proposal_matches)
        self.assertFalse(evidence_matches)

    def test_missing_or_non_string_self_integrity_members_do_not_verify(self) -> None:
        # Arrange
        proposal = {"canonicalizationVersion": 1}
        decision = {
            "canonicalizationVersion": 1,
            "evidenceDecisionDigest": 123,
        }

        # Act
        proposal_matches = digest.proposal_digest_matches(proposal)
        evidence_matches = digest.evidence_decision_digest_matches(decision)

        # Assert
        self.assertFalse(proposal_matches)
        self.assertFalse(evidence_matches)


class ApplicationDigestFixtureTests(unittest.TestCase):
    def test_every_source_event_binding_names_and_digests_the_canonical_salient_event(self) -> None:
        # Arrange
        source = _fixture(GOLDEN_ROOT / "event" / "drone-event-salient" / "baseline.json")
        source_id = cast("str", source["id"])
        source_digest = digest.source_event_digest(parse_envelope(source))
        bindings = tuple(
            (path, mapping)
            for path in sorted(GOLDEN_ROOT.rglob("*.json"))
            for mapping in _mappings(_fixture(path))
            if "sourceEventId" in mapping or "sourceEventDigest" in mapping
        )

        # Act
        actual = tuple(
            (mapping.get("sourceEventId"), mapping.get("sourceEventDigest"))
            for _, mapping in bindings
        )

        # Assert
        self.assertTrue(bindings)
        for (path, _), binding in zip(bindings, actual, strict=True):
            with self.subTest(path=path):
                self.assertEqual((source_id, source_digest), binding)

    def test_every_proposal_fixture_carries_its_recomputed_self_digest(self) -> None:
        # Arrange
        paths = sorted(
            (
                *((GOLDEN_ROOT / "payload" / "agent-proposal").glob("*.json")),
                *((GOLDEN_ROOT / "event" / "agent-proposal").glob("*.json")),
            )
        )

        # Act
        documents = tuple((path, _payload(_fixture(path))) for path in paths)

        # Assert
        self.assertTrue(documents)
        for path, document in documents:
            with self.subTest(path=path):
                self.assertTrue(digest.proposal_digest_matches(document))

    def test_every_evidence_decision_fixture_carries_its_recomputed_self_digest(self) -> None:
        # Arrange
        paths = sorted(
            (
                *((GOLDEN_ROOT / "payload" / "evidence-decision").glob("*.json")),
                *((GOLDEN_ROOT / "event" / "evidence-decision").glob("*.json")),
            )
        )

        # Act
        documents = tuple((path, _payload(_fixture(path))) for path in paths)

        # Assert
        self.assertTrue(documents)
        for path, document in documents:
            with self.subTest(path=path):
                self.assertTrue(digest.evidence_decision_digest_matches(document))

    def test_every_downstream_fixture_binds_the_canonical_proposal_and_decision_digests(
        self,
    ) -> None:
        # Arrange
        proposal = _fixture(GOLDEN_ROOT / "payload" / "agent-proposal" / "baseline.json")
        decisions = tuple(
            _fixture(path)
            for path in sorted((GOLDEN_ROOT / "payload" / "evidence-decision").glob("*.json"))
            if "unknown-member" not in path.name
        )
        proposal_digests = {cast("str", proposal["proposalId"]): digest.proposal_digest(proposal)}
        decision_digests = {
            cast("str", decision["evidenceDecisionId"]): digest.evidence_decision_digest(decision)
            for decision in decisions
        }
        self_digest_roots = {
            GOLDEN_ROOT / "payload" / "agent-proposal",
            GOLDEN_ROOT / "event" / "agent-proposal",
            GOLDEN_ROOT / "payload" / "evidence-decision",
            GOLDEN_ROOT / "event" / "evidence-decision",
        }
        paths = tuple(
            path
            for path in sorted(GOLDEN_ROOT.rglob("*.json"))
            if path.parent not in self_digest_roots
        )

        # Act
        bindings = tuple(
            (path, mapping)
            for path in paths
            for mapping in _mappings(_fixture(path))
            if "proposalDigest" in mapping or "evidenceDecisionDigest" in mapping
        )

        # Assert
        self.assertTrue(bindings)
        for path, mapping in bindings:
            with self.subTest(path=path, mapping=mapping):
                proposal_id = mapping.get("proposalId")
                if proposal_id in proposal_digests:
                    self.assertEqual(
                        proposal_digests[cast("str", proposal_id)], mapping["proposalDigest"]
                    )
                decision_id = mapping.get("evidenceDecisionId")
                if decision_id in decision_digests:
                    self.assertEqual(
                        decision_digests[cast("str", decision_id)],
                        mapping["evidenceDecisionDigest"],
                    )


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
