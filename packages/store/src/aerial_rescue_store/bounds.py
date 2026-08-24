"""Every wait the durable store is allowed to make, and the relations between them.

[ADR-0085](../../../../docs/adr/0085-bound-every-durable-store-wait.md) derives each value from
a number the repository already carries and records why. What lives here is the arithmetic
behind them, enforced where a set is built rather than asserted in prose.

Measured on the pinned cluster on 2026-08-23, ``statement_timeout``, ``lock_timeout``, and
``idle_in_transaction_session_timeout`` are all ``0`` -- not conservative defaults but no bound
at all. A statement runs forever, a lock waits forever, and an open transaction holds its rows
forever. The last is reachable by design rather than by accident: the approval-consumption
sequence keeps the transaction open across the command gateway's clock reads and its call into
the domain, so the store deliberately hands control back to a caller while holding a row lock.

The constants are what a composition root is expected to supply. They are not defaults:
``EngineBounds`` takes every member with none, so a root that forgets one fails to construct.

This module is pure. It performs no input or output, reads no clock, and opens nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_store import StoreError

POOL_SIZE: Final = 5
"""Sessions per process, below both the demand and the cluster ceilings ADR-0085 derives."""

POOL_OVERFLOW: Final = 0
"""Refuse rather than queue without bound, as the direct publisher's buffer capacity does."""

CHECKOUT_TIMEOUT_SECONDS: Final = 2
"""The connected command path's own p95 target, so exhaustion shows up in about that time."""

CONNECT_TIMEOUT_SECONDS: Final = 5

CONNECT_RETRIES: Final = 0
"""An absent database fails the caller, as an absent broker does."""

STATEMENT_TIMEOUT_MILLISECONDS: Final = 5_000
LOCK_TIMEOUT_MILLISECONDS: Final = 5_000

IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS: Final = 15_000
"""Contains one lock wait plus one statement, with margin, and is far below the approval life."""

SHUTDOWN_GRACE_SECONDS: Final = 15
MIGRATION_WAIT_SECONDS: Final = 90
"""Contains the cluster's own healthcheck envelope: a 10 s start period then twelve 5 s probes."""

SERVER_DEADLOCK_TIMEOUT_MILLISECONDS: Final = 1_000
"""The cluster's ``deadlock_timeout``, measured rather than assumed. The lock wait exceeds it."""

_POSITIVE_MEMBERS: Final = (
    "pool_size",
    "checkout_timeout_seconds",
    "connect_timeout_seconds",
    "statement_timeout_milliseconds",
    "lock_timeout_milliseconds",
    "idle_in_transaction_timeout_milliseconds",
    "shutdown_grace_seconds",
)
_NON_NEGATIVE_MEMBERS: Final = ("pool_overflow", "connect_retries")


class BoundsRefusal(Enum):
    """Why a set of bounds is not usable."""

    NOT_POSITIVE = "every duration and the pool size must be positive"
    NEGATIVE = "the pool overflow and the connect retry count may be zero but never negative"
    LOCK_BELOW_DEADLOCK_DETECTION = (
        "the lock wait must exceed the server's deadlock detection interval, or a deadlock is "
        "reported as an ordinary contended wait"
    )
    TRANSACTION_BELOW_ITS_PARTS = (
        "the idle-in-transaction bound must contain one lock wait plus one statement"
    )


class BoundsError(StoreError):
    """A set of bounds this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class EngineBounds:
    """Every wait one engine is allowed to make, refusing a set whose arithmetic is wrong."""

    pool_size: int
    pool_overflow: int
    checkout_timeout_seconds: int
    connect_timeout_seconds: int
    connect_retries: int
    statement_timeout_milliseconds: int
    lock_timeout_milliseconds: int
    idle_in_transaction_timeout_milliseconds: int
    shutdown_grace_seconds: int

    def __post_init__(self) -> None:
        """Refuse a degenerate member and then the two relations that carry the meaning.

        Raises:
            BoundsError: With ``NOT_POSITIVE`` or ``NEGATIVE`` naming the offending member, with
                ``LOCK_BELOW_DEADLOCK_DETECTION`` when a deadlock could not be told from
                contention, or with ``TRANSACTION_BELOW_ITS_PARTS`` when the transaction-level
                bound would end a transaction that is behaving legally.
        """
        for member in _POSITIVE_MEMBERS:
            if getattr(self, member) < 1:
                raise BoundsError(BoundsRefusal.NOT_POSITIVE, member)
        for member in _NON_NEGATIVE_MEMBERS:
            if getattr(self, member) < 0:
                raise BoundsError(BoundsRefusal.NEGATIVE, member)
        if self.lock_timeout_milliseconds <= SERVER_DEADLOCK_TIMEOUT_MILLISECONDS:
            raise BoundsError(
                BoundsRefusal.LOCK_BELOW_DEADLOCK_DETECTION, self.lock_timeout_milliseconds
            )
        parts = self.lock_timeout_milliseconds + self.statement_timeout_milliseconds
        if self.idle_in_transaction_timeout_milliseconds < parts:
            raise BoundsError(
                BoundsRefusal.TRANSACTION_BELOW_ITS_PARTS,
                self.idle_in_transaction_timeout_milliseconds,
            )
