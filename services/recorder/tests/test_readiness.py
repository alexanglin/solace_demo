from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_recorder.readiness import (
    EXPIRY_SECONDS,
    LEASE_VERSION,
    MAXIMUM_LEASE_BYTES,
    REFRESH_SECONDS,
    LeaseRefusal,
    ReadinessLease,
    check_lease,
    main,
    readiness_reasons,
)

pytestmark = [pytest.mark.unit]


class RecorderReadinessLeaseTests(unittest.TestCase):
    def test_activation_and_due_refresh_use_atomic_canonical_integer_documents(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        epochs = iter((1_000, 1_001))
        monotonic = iter((20, 20 + REFRESH_SECONDS - 1, 20 + REFRESH_SECONDS))
        lease = ReadinessLease(path, lambda: next(epochs), lambda: next(monotonic))
        replacements: list[tuple[Path, Path]] = []
        real_replace = Path.replace

        def replace(source: Path, target: Path) -> Path:
            replacements.append((source, target))
            return real_replace(source, target)

        # Act
        with patch.object(Path, "replace", autospec=True, side_effect=replace):
            lease.activate()
            lease.refresh_if_due()
            lease.refresh_if_due()
        raw = path.read_bytes()
        document = canonical.decode(raw)

        # Assert
        self.assertEqual(
            {
                "readinessVersion": LEASE_VERSION,
                "updatedAtEpochSeconds": 1_001,
            },
            document,
        )
        self.assertEqual(raw, canonical.canonical_bytes(document))
        self.assertEqual([path, path], [target for _source, target in replacements])
        self.assertTrue(all(source.parent == root for source, _target in replacements))
        self.assertEqual((), tuple(root.glob(".recorder-ready-*")))

    def test_check_refuses_missing_stale_future_malformed_noncanonical_and_nonregular_leases(
        self,
    ) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        now = 10_000
        cases: list[tuple[str, bytes | None, LeaseRefusal]] = [
            ("missing", None, LeaseRefusal.MISSING),
            (
                "stale",
                canonical.canonical_bytes(
                    {
                        "readinessVersion": LEASE_VERSION,
                        "updatedAtEpochSeconds": now - EXPIRY_SECONDS - 1,
                    }
                ),
                LeaseRefusal.STALE,
            ),
            (
                "future",
                canonical.canonical_bytes(
                    {
                        "readinessVersion": LEASE_VERSION,
                        "updatedAtEpochSeconds": now + 1,
                    }
                ),
                LeaseRefusal.TIMESTAMP,
            ),
            ("malformed", b"{", LeaseRefusal.DOCUMENT),
            (
                "noncanonical",
                b'{"updatedAtEpochSeconds":10000, "readinessVersion":"recorder-readiness/v1"}',
                LeaseRefusal.DOCUMENT,
            ),
        ]
        refusals: list[LeaseRefusal | None] = []

        # Act
        for _name, raw, _expected in cases:
            path.unlink(missing_ok=True)
            if raw is not None:
                path.write_bytes(raw)
            refusals.append(check_lease(path, now_epoch_seconds=now))
        path.unlink()
        path.mkdir()
        refusals.append(check_lease(path, now_epoch_seconds=now))
        path.rmdir()
        path.write_bytes(b"x" * (MAXIMUM_LEASE_BYTES + 1))
        refusals.append(check_lease(path, now_epoch_seconds=now))

        # Assert
        self.assertEqual(
            [
                LeaseRefusal.MISSING,
                LeaseRefusal.STALE,
                LeaseRefusal.TIMESTAMP,
                LeaseRefusal.DOCUMENT,
                LeaseRefusal.DOCUMENT,
                LeaseRefusal.PATH,
                LeaseRefusal.SIZE,
            ],
            refusals,
        )

    def test_fresh_lease_has_no_public_readiness_reason_and_close_removes_it(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        lease = ReadinessLease(path, lambda: 2_000, lambda: 30)

        # Act
        lease.activate()
        reasons = readiness_reasons(path, now_epoch_seconds=2_000)
        lease.close()
        after_close = readiness_reasons(path, now_epoch_seconds=2_000)

        # Assert
        self.assertEqual((), reasons)
        self.assertEqual(("recorder-capture-unavailable",), after_close)
        self.assertFalse(path.exists())

    def test_healthcheck_cli_reports_only_freshness_without_disclosing_the_path(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "ready.json"
        out = io.StringIO()
        error = io.StringIO()
        lease = ReadinessLease(path, lambda: 3_000, lambda: 40)
        lease.activate()

        # Act
        with patch("aerial_rescue_recorder.readiness._epoch_seconds", return_value=3_000):
            ready = main(("--check", str(path)), out=out, error=error)
        path.unlink()
        with patch("aerial_rescue_recorder.readiness._epoch_seconds", return_value=3_000):
            missing = main(("--check", str(path)), out=out, error=error)

        # Assert
        self.assertEqual((0, 1), (ready, missing))
        self.assertEqual("ready\n", out.getvalue())
        self.assertEqual("not ready\n", error.getvalue())
        self.assertNotIn(str(path), out.getvalue() + error.getvalue())
