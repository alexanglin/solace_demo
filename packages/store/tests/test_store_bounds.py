"""The nesting relations ADR-0085 decides, checked where a bounds set is built.

The individual numbers are the record's; what this module proves is that a set violating the
arithmetic behind them cannot be constructed at all. Three relations are load-bearing: every
duration is positive, the lock wait exceeds the server's deadlock detection so a deadlock and a
contended wait stay distinguishable, and the idle-in-transaction bound contains the longest
legal transaction, which is one lock wait plus one statement.

The last test assembles the constants this module publishes and holds them to the same
relations, so the values a composition root is expected to supply cannot drift out of the shape
the record derived.

The file is ``test_store_bounds`` rather than ``test_bounds`` because a test basename has to be
unique across the repository: these directories carry no ``__init__.py``, so whole-project mypy
maps both to a top-level module and refuses the duplicate, and ``tools/affected_tests`` marks an
ambiguous module opaque and widens every commit that touches either file to the whole suite.
``services/fleet_simulator/tests/test_bounds.py`` holds the name.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from enum import Enum
from typing import Final

import pytest
from aerial_rescue_store.bounds import (
    CHECKOUT_TIMEOUT_SECONDS,
    CONNECT_RETRIES,
    CONNECT_TIMEOUT_SECONDS,
    IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    LOCK_TIMEOUT_MILLISECONDS,
    MIGRATION_WAIT_SECONDS,
    POOL_OVERFLOW,
    POOL_SIZE,
    SERVER_DEADLOCK_TIMEOUT_MILLISECONDS,
    SHUTDOWN_GRACE_SECONDS,
    STATEMENT_TIMEOUT_MILLISECONDS,
    BoundsError,
    BoundsRefusal,
    EngineBounds,
)

SUPPLIED: Final = EngineBounds(
    pool_size=POOL_SIZE,
    pool_overflow=POOL_OVERFLOW,
    checkout_timeout_seconds=CHECKOUT_TIMEOUT_SECONDS,
    connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
    connect_retries=CONNECT_RETRIES,
    statement_timeout_milliseconds=STATEMENT_TIMEOUT_MILLISECONDS,
    lock_timeout_milliseconds=LOCK_TIMEOUT_MILLISECONDS,
    idle_in_transaction_timeout_milliseconds=IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
)

POSITIVE_MEMBERS: Final = (
    "pool_size",
    "checkout_timeout_seconds",
    "connect_timeout_seconds",
    "statement_timeout_milliseconds",
    "lock_timeout_milliseconds",
    "idle_in_transaction_timeout_milliseconds",
    "shutdown_grace_seconds",
)
NON_NEGATIVE_MEMBERS: Final = ("pool_overflow", "connect_retries")


def _refusal(**changes: int) -> Enum | None:
    """Return the refusal a change to the supplied set produces, or None when accepted."""
    try:
        replace(SUPPLIED, **changes)
    except BoundsError as error:
        return error.refusal
    return None


class PositiveMemberTests(unittest.TestCase):
    def test_every_duration_and_the_pool_size_is_refused_at_zero_by_its_own_name(self) -> None:
        # Arrange
        members = POSITIVE_MEMBERS

        # Act
        refused = tuple(_refusal(**{member: 0}) for member in members)

        # Assert
        self.assertEqual((BoundsRefusal.NOT_POSITIVE,) * len(members), refused)

    def test_a_negative_duration_is_refused_as_well_as_a_zero_one(self) -> None:
        # Arrange
        member = "statement_timeout_milliseconds"

        # Act
        with pytest.raises(BoundsError) as captured:
            replace(SUPPLIED, **{member: -1})

        # Assert
        self.assertEqual(
            (BoundsRefusal.NOT_POSITIVE, member), (captured.value.refusal, captured.value.value)
        )

    def test_the_overflow_and_the_retry_count_are_accepted_at_zero(self) -> None:
        # Arrange
        members = NON_NEGATIVE_MEMBERS

        # Act
        refused = tuple(_refusal(**{member: 0}) for member in members)

        # Assert
        self.assertEqual((None,) * len(members), refused)

    def test_a_negative_overflow_or_retry_count_is_refused_by_its_own_name(self) -> None:
        # Arrange
        members = NON_NEGATIVE_MEMBERS

        # Act
        refused = tuple(_refusal(**{member: -1}) for member in members)

        # Assert
        self.assertEqual((BoundsRefusal.NEGATIVE,) * len(members), refused)


class NestingTests(unittest.TestCase):
    def test_a_lock_wait_at_the_deadlock_detection_interval_is_refused(self) -> None:
        # Arrange
        equal = SERVER_DEADLOCK_TIMEOUT_MILLISECONDS

        # Act
        with pytest.raises(BoundsError) as captured:
            replace(SUPPLIED, lock_timeout_milliseconds=equal)

        # Assert
        self.assertEqual(
            (BoundsRefusal.LOCK_BELOW_DEADLOCK_DETECTION, equal),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_lock_wait_one_millisecond_above_the_detection_interval_is_accepted(self) -> None:
        # Arrange
        above = SERVER_DEADLOCK_TIMEOUT_MILLISECONDS + 1

        # Act
        refusal = _refusal(lock_timeout_milliseconds=above)

        # Assert
        self.assertIsNone(refusal)

    def test_a_transaction_bound_below_a_lock_wait_plus_a_statement_is_refused(self) -> None:
        # Arrange
        parts = LOCK_TIMEOUT_MILLISECONDS + STATEMENT_TIMEOUT_MILLISECONDS

        # Act
        with pytest.raises(BoundsError) as captured:
            replace(SUPPLIED, idle_in_transaction_timeout_milliseconds=parts - 1)

        # Assert
        self.assertEqual(
            (BoundsRefusal.TRANSACTION_BELOW_ITS_PARTS, parts - 1),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_transaction_bound_exactly_equal_to_its_parts_is_accepted(self) -> None:
        # Arrange
        parts = LOCK_TIMEOUT_MILLISECONDS + STATEMENT_TIMEOUT_MILLISECONDS

        # Act
        refusal = _refusal(idle_in_transaction_timeout_milliseconds=parts)

        # Assert
        self.assertIsNone(refusal)


class SuppliedConstantTests(unittest.TestCase):
    def test_the_constants_this_module_publishes_satisfy_every_relation(self) -> None:
        # Arrange
        supplied = SUPPLIED

        # Act
        relations = (
            supplied.lock_timeout_milliseconds > SERVER_DEADLOCK_TIMEOUT_MILLISECONDS,
            supplied.idle_in_transaction_timeout_milliseconds
            >= supplied.lock_timeout_milliseconds + supplied.statement_timeout_milliseconds,
            supplied.shutdown_grace_seconds * 1000
            >= supplied.idle_in_transaction_timeout_milliseconds,
        )

        # Assert
        self.assertEqual((True, True, True), relations)

    def test_the_migration_wait_contains_the_clusters_own_healthcheck_envelope(self) -> None:
        # Arrange
        envelope_seconds = 10 + 12 * 5

        # Act
        contains = envelope_seconds < MIGRATION_WAIT_SECONDS

        # Assert
        self.assertTrue(contains)


if __name__ == "__main__":
    unittest.main()
