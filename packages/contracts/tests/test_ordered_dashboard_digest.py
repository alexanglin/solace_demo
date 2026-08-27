"""Domain-separated witness digest for audit-ordered dashboard events."""

from __future__ import annotations

import unittest

from aerial_rescue_contracts.digest import Context, digest, ordered_dashboard_event_digest


class OrderedDashboardDigestTests(unittest.TestCase):
    def test_specialized_digest_matches_the_committed_cross_language_anchor(self) -> None:
        # Arrange
        event = {
            "kind": "missionLifecycle",
            "eventClass": "MISSION",
            "mission": "mission-synthetic-0001",
            "time": "2026-08-24T12:00:00.000Z",
            "data": {"lifecycle": "PLANNED"},
        }
        witness = {"canonicalizationVersion": 1, "auditOrdinal": 1, "event": event}

        # Act
        specialized = ordered_dashboard_event_digest(1, event)
        generic = digest(Context.ORDERED_DASHBOARD_EVENT, witness)

        # Assert
        self.assertEqual(
            "eafd46f76f706183272a016f99d5468c7ebde22de44600092f81992903509c25", specialized
        )
        self.assertEqual(generic, specialized)

    def test_the_witness_covers_the_ordinal_and_all_five_event_fields(self) -> None:
        # Arrange
        event = {
            "kind": "missionLifecycle",
            "eventClass": "MISSION",
            "mission": "mission-synthetic-0001",
            "time": "2026-08-24T12:00:00.000Z",
            "data": {"lifecycle": "PLANNED"},
        }
        replacements = {
            "kind": "sectorLifecycle",
            "eventClass": "CONNECTIVITY",
            "mission": "mission-synthetic-0002",
            "time": "2026-08-24T12:00:00.001Z",
            "data": {"lifecycle": "SEARCHING"},
        }
        baseline = ordered_dashboard_event_digest(1, event)

        # Act
        observed = [ordered_dashboard_event_digest(2, event)]
        for field, replacement in replacements.items():
            observed.append(ordered_dashboard_event_digest(1, {**event, field: replacement}))

        # Assert
        self.assertTrue(all(candidate != baseline for candidate in observed))


if __name__ == "__main__":
    unittest.main()
