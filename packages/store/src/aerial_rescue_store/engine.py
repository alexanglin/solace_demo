"""The one module that names SQLAlchemy, and the place every bound becomes an instruction.

[ADR-0090](../../../../docs/adr/0090-bound-the-lock-wait-below-the-statement-time.md) sets the
values; this module is where they reach a driver. The decision of *what* to hand over is pure
and lives in ``engine_arguments``, so every bound is asserted without a database;
``create_engine`` is the thin call that passes the result on.

**Nothing here imports ``asyncpg``.** It is a runtime dependency reached only through the
dialect named in the URL, and failures are discriminated on typed ``sqlalchemy.exc`` classes.
That is deliberate: ``asyncpg`` ships no ``py.typed`` marker, so importing it would need the
same narrow relaxation ``docs/adr/0028`` had to grant the Solace client, and not importing it
costs nothing.

The credential travels inside a SQLAlchemy ``URL``, which holds it as a member and masks it in
both ``str`` and ``repr``. That is the same structural separation ``settings`` makes, carried
one layer further rather than re-established: no string this module builds contains the
password.

Three of the four server-side bounds are applied per session through the connection's
``server_settings``, not on the cluster. A cluster-wide setting would apply this member's bounds
to ``psql``, to the migration runner, and to every later consumer that needs different ones.

The isolation level is stated for the same reason the bounds are.
[ADR-0089](../../../../docs/adr/0089-state-read-committed-rather-than-inherit-it.md) records why
it cannot be left to the cluster: the audit ordinal's ordering rests on a waiting appender, and
under ``REPEATABLE READ`` the second appender does not wait -- it is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from aerial_rescue_store import StoreError
from aerial_rescue_store.settings import DRIVER

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aerial_rescue_store.bounds import EngineBounds
    from aerial_rescue_store.settings import DatabaseSettings

CONNECT_TIMEOUT: Final = "timeout"
SERVER_SETTINGS: Final = "server_settings"

ISOLATION_LEVEL: Final = "READ COMMITTED"
"""Stated rather than inherited, because ADR-0088's ordering depends on it (ADR-0089)."""

STATEMENT_TIMEOUT_SETTING: Final = "statement_timeout"
LOCK_TIMEOUT_SETTING: Final = "lock_timeout"
IDLE_IN_TRANSACTION_SETTING: Final = "idle_in_transaction_session_timeout"


class EngineRefusal(Enum):
    """Why a target and a bounds set do not make an engine."""

    UNSUPPORTED_RETRIES = (
        "this adapter has no connect retry loop, so a non-zero retry count would be ignored "
        "rather than honoured"
    )


class EngineError(StoreError):
    """A target or bound this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class EngineArguments:
    """Exactly what one engine is built from, with the credential only inside the URL."""

    url: URL
    pool_size: int
    max_overflow: int
    pool_timeout: int
    isolation_level: str
    connect_args: Mapping[str, object]


def engine_url(settings: DatabaseSettings) -> URL:
    """Return the structured URL for this target, which masks the credential when rendered."""
    return URL.create(
        drivername=DRIVER,
        username=settings.user,
        password=settings.password,
        host=settings.host,
        port=settings.port,
        database=settings.database,
    )


def engine_arguments(settings: DatabaseSettings, bounds: EngineBounds) -> EngineArguments:
    """Return the arguments one engine is built from, refusing a bound nothing consumes.

    Args:
        settings: Where the cluster is, who connects, and the credential.
        bounds: Every wait the engine is allowed to make.

    Returns:
        The pool arguments and the per-session connect arguments, ready to hand to the driver.

    Raises:
        EngineError: With ``UNSUPPORTED_RETRIES`` when the retry count is not zero. A bound that
            reaches nothing is worse than no bound, so it is refused rather than dropped.
    """
    if bounds.connect_retries != 0:
        raise EngineError(EngineRefusal.UNSUPPORTED_RETRIES, bounds.connect_retries)
    return EngineArguments(
        url=engine_url(settings),
        pool_size=bounds.pool_size,
        max_overflow=bounds.pool_overflow,
        pool_timeout=bounds.checkout_timeout_seconds,
        isolation_level=ISOLATION_LEVEL,
        connect_args={
            CONNECT_TIMEOUT: bounds.connect_timeout_seconds,
            SERVER_SETTINGS: {
                STATEMENT_TIMEOUT_SETTING: str(bounds.statement_timeout_milliseconds),
                LOCK_TIMEOUT_SETTING: str(bounds.lock_timeout_milliseconds),
                IDLE_IN_TRANSACTION_SETTING: str(bounds.idle_in_transaction_timeout_milliseconds),
            },
        },
    )


def create_engine(settings: DatabaseSettings, bounds: EngineBounds) -> AsyncEngine:
    """Return a lazily constructed engine for this target, opening no connection.

    Args:
        settings: Where the cluster is, who connects, and the credential.
        bounds: Every wait the engine is allowed to make.

    Returns:
        The engine. Its pool is empty until something asks it for a connection, so an
        unreachable target fails at the first use rather than here.

    Raises:
        EngineError: With ``UNSUPPORTED_RETRIES``, as ``engine_arguments`` does.
    """
    arguments = engine_arguments(settings, bounds)
    return create_async_engine(
        arguments.url,
        pool_size=arguments.pool_size,
        max_overflow=arguments.max_overflow,
        pool_timeout=arguments.pool_timeout,
        isolation_level=arguments.isolation_level,
        connect_args=dict(arguments.connect_args),
    )
