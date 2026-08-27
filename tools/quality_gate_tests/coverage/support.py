"""Shared Arrange builders for TypeScript coverage-gate tests."""

from __future__ import annotations

from collections.abc import Mapping

MetricCounts = tuple[object, object, object, object]


def coverage_summary(
    metrics: tuple[str, ...],
    defaults: MetricCounts,
    overrides: Mapping[str, MetricCounts] | None = None,
) -> dict[str, object]:
    """Arrange one coverage-summary measurement with optional metric overrides."""
    counts = {metric: defaults for metric in metrics}
    if overrides is not None:
        counts.update(overrides)
    return {
        metric: {
            "total": values[0],
            "covered": values[1],
            "skipped": values[2],
            "pct": values[3],
        }
        for metric, values in counts.items()
    }
