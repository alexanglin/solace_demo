from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_observability.freshness import (
    EXPIRY_SECONDS,
    LEASE_VERSION,
    FreshnessLease,
    FreshnessLeaseError,
    LeaseRefusal,
    check_lease,
    epoch_seconds,
    monotonic_seconds,
)

pytestmark = [pytest.mark.unit]


class FreshnessLeaseTests(unittest.TestCase):
    def test_default_clocks_are_integer_sources(self) -> None:
        # Arrange
        sources = (epoch_seconds, monotonic_seconds)

        # Act
        readings = tuple(source() for source in sources)

        # Assert
        self.assertTrue(all(type(reading) is int and reading >= 0 for reading in readings))

    def test_active_lease_is_canonical_fresh_and_removed_on_close(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        lease = FreshnessLease(path, lambda: 1_000, lambda: 5)

        # Act
        lease.activate()
        raw = path.read_bytes()
        refusal = check_lease(path, now_epoch_seconds=1_000)
        lease.close()
        missing = check_lease(path, now_epoch_seconds=1_000)

        # Assert
        self.assertEqual(
            {
                "readinessVersion": LEASE_VERSION,
                "updatedAtEpochSeconds": 1_000,
            },
            canonical.decode(raw),
        )
        self.assertIsNone(refusal)
        self.assertEqual(LeaseRefusal.MISSING, missing)

    def test_stale_malformed_and_future_documents_fail_closed(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        now = 2_000
        documents = (
            canonical.canonical_bytes(
                {
                    "readinessVersion": LEASE_VERSION,
                    "updatedAtEpochSeconds": now - EXPIRY_SECONDS - 1,
                }
            ),
            b"{",
            canonical.canonical_bytes(
                {
                    "readinessVersion": LEASE_VERSION,
                    "updatedAtEpochSeconds": now + 1,
                }
            ),
        )
        refusals: list[LeaseRefusal | None] = []

        # Act
        for document in documents:
            path.write_bytes(document)
            refusals.append(check_lease(path, now_epoch_seconds=now))

        # Assert
        self.assertEqual(
            [LeaseRefusal.STALE, LeaseRefusal.DOCUMENT, LeaseRefusal.TIMESTAMP],
            refusals,
        )

    def test_inactive_lease_and_unusable_parent_refuse_writes(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        missing_parent = root / "missing" / "ready.json"
        regular_parent = root / "regular"
        regular_parent.write_text("not a directory", encoding="utf-8")
        leases = (
            FreshnessLease(root / "inactive.json", lambda: 1_000, lambda: 5),
            FreshnessLease(missing_parent, lambda: 1_000, lambda: 5),
            FreshnessLease(regular_parent / "ready.json", lambda: 1_000, lambda: 5),
        )
        messages: list[str] = []

        # Act
        for operation in (leases[0].refresh_if_due, leases[1].activate, leases[2].activate):
            with pytest.raises(FreshnessLeaseError) as captured:
                operation()
            messages.append(str(captured.value))

        # Assert
        self.assertEqual(
            [
                "freshness lease is not active",
                "readiness directory is unavailable",
                "readiness directory is unavailable",
            ],
            messages,
        )

    def test_shape_version_timestamp_and_symlink_refusals_are_distinct(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        target = root / "target.json"
        now = 2_000
        documents = (
            canonical.canonical_bytes({"readinessVersion": LEASE_VERSION}),
            canonical.canonical_bytes(
                {"readinessVersion": "recorder-readiness/v2", "updatedAtEpochSeconds": now}
            ),
            canonical.canonical_bytes(
                {"readinessVersion": LEASE_VERSION, "updatedAtEpochSeconds": True}
            ),
        )
        refusals: list[LeaseRefusal | None] = []

        # Act
        for document in documents:
            path.write_bytes(document)
            refusals.append(check_lease(path, now_epoch_seconds=now))
        path.unlink()
        target.write_bytes(documents[0])
        path.symlink_to(target)
        refusals.append(check_lease(path, now_epoch_seconds=now))

        # Assert
        self.assertEqual(
            [
                LeaseRefusal.DOCUMENT,
                LeaseRefusal.VERSION,
                LeaseRefusal.TIMESTAMP,
                LeaseRefusal.PATH,
            ],
            refusals,
        )
