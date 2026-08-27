"""The unit of work: a session factory, a bounded transaction, and an explicit shutdown.

`engine.py` builds a pool. This module is what opens work over it, and it is the second and
last module in this member permitted to name SQLAlchemy.

Two session defaults are overridden, and both overrides are decisions rather than taste.
``expire_on_commit`` is off because this member's callers map rows into typed values and then
commit; leaving it on would expire those values and make reading one of them issue a second
round trip -- against a session whose transaction has already ended. ``autoflush`` is off
because everything here is a Core statement issued deliberately, so an implicit flush would be
a write nobody asked for, at a moment nobody chose.

The transaction boundary is written as an explicit ``try`` rather than as
``async with session.begin()`` for one reason: what it does on each exit is the behaviour under
test, and writing it out is what lets a deterministic fake observe it. It catches
``BaseException`` deliberately, because ``asyncio.CancelledError`` is one and a cancelled caller
must not leave a transaction open holding the audit sequence row; the exception is re-raised
unchanged, so cancellation still propagates.

The shutdown is the only wait in this member that is bounded client-side.
[ADR-0090](../../../../docs/adr/0090-bound-the-lock-wait-below-the-statement-time.md) rejects a
client-side bound for a *statement*, because cancelling the coroutine abandons the wait and
leaves the server executing. A shutdown grace is the opposite case: there is no server-side
setting for "stop waiting for the pool to drain", so the bound has nowhere else to live.

Nothing here connects. Building a factory is as lazy as building the engine under it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aerial_rescue_store import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

EXPIRE_ON_COMMIT: Final = False
"""A committed typed value stays readable without a second round trip."""

AUTOFLUSH: Final = False
"""This member issues Core statements deliberately; an implicit flush is an unasked-for write."""


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return the factory that opens sessions on ``engine``, opening no connection.

    Args:
        engine: The bound pool. Its lifetime belongs to the composition root that built it.

    Returns:
        The factory. It holds the engine and nothing else; the pool stays empty until a
        session asks it for a connection.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=EXPIRE_ON_COMMIT,
        autoflush=AUTOFLUSH,
    )


class SessionRefusal(Enum):
    """Why a unit of work did not end the way it was asked to."""

    SHUTDOWN_TIMED_OUT = (
        "the pool did not release every connection within the shutdown grace, so the process "
        "stops waiting rather than outliving the longest transaction the server tolerates"
    )


class SessionError(StoreError):
    """A shutdown this module refuses to keep waiting on, carrying the refusal as data."""


class DurableSession(Protocol):
    """What a transaction boundary needs of a session, and nothing more."""

    async def commit(self) -> None:
        """End the transaction, making its writes durable."""

    async def rollback(self) -> None:
        """End the transaction, discarding its writes."""

    async def close(self) -> None:
        """Release the session and return its connection to the pool."""


class Disposable(Protocol):
    """What an explicit shutdown needs of a pool, and nothing more."""

    async def dispose(self) -> None:
        """Release every connection the pool holds."""


@asynccontextmanager
async def transaction[SessionT: DurableSession](
    factory: Callable[[], SessionT],
) -> AsyncIterator[SessionT]:
    """Open one unit of work that the caller cannot leave open.

    Args:
        factory: What makes the session. Injected rather than named, so the boundary's own
            behaviour is provable without a database.

    Yields:
        The session, for the duration of the body.

    Raises:
        BaseException: Whatever the body raised, unchanged and after the rollback. A
            cancellation is included on purpose: it must not leave a transaction open.
    """
    session = factory()
    try:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        await session.commit()
    finally:
        await session.close()


async def close(pool: Disposable, grace_seconds: int) -> None:
    """Release every connection ``pool`` holds, or stop waiting and say so.

    Args:
        pool: The engine or pool to dispose.
        grace_seconds: The bound from ``EngineBounds``, supplied by the composition root.

    Raises:
        SessionError: With ``SHUTDOWN_TIMED_OUT`` when the grace elapses first. A shutdown that
            waited without bound would outlive the longest transaction the server tolerates,
            which is the number ADR-0090 derives this grace from.
    """
    try:
        async with asyncio.timeout(grace_seconds):
            await pool.dispose()
    except TimeoutError as exhausted:
        raise SessionError(SessionRefusal.SHUTDOWN_TIMED_OUT, grace_seconds) from exhausted
