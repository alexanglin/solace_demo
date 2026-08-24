"""The engine's arguments, which are where every bound ADR-0085 sets becomes an instruction.

The interesting part is pure and is tested as such: ``engine_arguments`` turns a target and a
bounds set into the exact values handed to the driver, so every one of them is asserted without
a database. ``create_engine`` is the thin call that passes them on, and the one property worth
proving about it is that constructing an engine connects to nothing -- asserted against a port
nothing listens on, so an eager connect would fail the test rather than quietly succeed against
the developer's own running cluster.

A bound that reaches nothing is worse than no bound, so a non-zero connect retry count is
refused here rather than silently dropped: this adapter has no retry loop for one to control.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Final, cast

import pytest
from aerial_rescue_store.bounds import (
    CHECKOUT_TIMEOUT_SECONDS,
    CONNECT_RETRIES,
    CONNECT_TIMEOUT_SECONDS,
    IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    LOCK_TIMEOUT_MILLISECONDS,
    POOL_OVERFLOW,
    POOL_SIZE,
    SHUTDOWN_GRACE_SECONDS,
    STATEMENT_TIMEOUT_MILLISECONDS,
    EngineBounds,
)
from aerial_rescue_store.engine import (
    IDLE_IN_TRANSACTION_SETTING,
    ISOLATION_LEVEL,
    LOCK_TIMEOUT_SETTING,
    SERVER_SETTINGS,
    STATEMENT_TIMEOUT_SETTING,
    EngineError,
    EngineRefusal,
    create_engine,
    engine_arguments,
    engine_url,
)
from aerial_rescue_store.settings import DatabaseSettings
from sqlalchemy.pool import QueuePool

CREDENTIAL: Final = "fixture-not-a-real-credential"
SETTINGS: Final = DatabaseSettings(
    host="127.0.0.1",
    port=5432,
    user="aerial_rescue",
    database="aerial_rescue",
    password=CREDENTIAL,
)
BOUNDS: Final = EngineBounds(
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
UNREACHABLE_PORT: Final = 1


class EngineUrlTests(unittest.TestCase):
    def test_the_url_holds_the_credential_apart_from_every_rendered_form(self) -> None:
        # Arrange
        settings = SETTINGS

        # Act
        url = engine_url(settings)

        # Assert
        self.assertEqual(
            (CREDENTIAL, False, False),
            (url.password, CREDENTIAL in str(url), CREDENTIAL in repr(url)),
        )

    def test_the_url_addresses_the_driver_and_the_target_the_settings_name(self) -> None:
        # Arrange
        settings = SETTINGS

        # Act
        url = engine_url(settings)

        # Assert
        self.assertEqual(
            ("postgresql+asyncpg", "aerial_rescue", "127.0.0.1", 5432, "aerial_rescue"),
            (url.drivername, url.username, url.host, url.port, url.database),
        )


class EngineArgumentTests(unittest.TestCase):
    def test_every_pool_bound_reaches_the_arguments(self) -> None:
        # Arrange
        bounds = BOUNDS

        # Act
        arguments = engine_arguments(SETTINGS, bounds)

        # Assert
        self.assertEqual(
            (bounds.pool_size, bounds.pool_overflow, bounds.checkout_timeout_seconds),
            (arguments.pool_size, arguments.max_overflow, arguments.pool_timeout),
        )

    def test_the_connect_timeout_reaches_the_connect_arguments(self) -> None:
        # Arrange
        bounds = BOUNDS

        # Act
        arguments = engine_arguments(SETTINGS, bounds)

        # Assert
        self.assertEqual(bounds.connect_timeout_seconds, arguments.connect_args["timeout"])

    def test_every_server_side_bound_reaches_the_session_as_a_string_of_milliseconds(self) -> None:
        # Arrange
        bounds = BOUNDS

        # Act
        settings = engine_arguments(SETTINGS, bounds).connect_args[SERVER_SETTINGS]

        # Assert
        self.assertEqual(
            {
                STATEMENT_TIMEOUT_SETTING: str(bounds.statement_timeout_milliseconds),
                LOCK_TIMEOUT_SETTING: str(bounds.lock_timeout_milliseconds),
                IDLE_IN_TRANSACTION_SETTING: str(bounds.idle_in_transaction_timeout_milliseconds),
            },
            settings,
        )

    def test_the_arguments_carry_the_credential_only_inside_the_url(self) -> None:
        # Arrange
        bounds = BOUNDS

        # Act
        arguments = engine_arguments(SETTINGS, bounds)

        # Assert
        self.assertEqual(
            (CREDENTIAL, False),
            (arguments.url.password, CREDENTIAL in repr(arguments.connect_args)),
        )

    def test_a_non_zero_connect_retry_count_is_refused_because_nothing_consumes_it(self) -> None:
        # Arrange
        retrying = replace(BOUNDS, connect_retries=1)

        # Act
        with pytest.raises(EngineError) as captured:
            engine_arguments(SETTINGS, retrying)

        # Assert
        self.assertEqual(
            (EngineRefusal.UNSUPPORTED_RETRIES, 1),
            (captured.value.refusal, captured.value.value),
        )


class IsolationLevelTests(unittest.TestCase):
    def test_the_isolation_level_is_stated_rather_than_left_to_the_driver(self) -> None:
        # Arrange
        bounds = BOUNDS

        # Act
        arguments = engine_arguments(SETTINGS, bounds)

        # Assert
        self.assertEqual(
            ("READ COMMITTED", ISOLATION_LEVEL), (ISOLATION_LEVEL, arguments.isolation_level)
        )


class EngineConstructionTests(unittest.TestCase):
    def test_creating_an_engine_against_a_dead_port_opens_no_connection(self) -> None:
        # Arrange
        unreachable = replace(SETTINGS, port=UNREACHABLE_PORT)

        # Act
        engine = create_engine(unreachable, BOUNDS)

        # Assert
        self.assertEqual(
            (0, UNREACHABLE_PORT),
            (cast("QueuePool", engine.pool).checkedout(), engine.url.port),
        )

    def test_the_created_engine_carries_the_pool_size_it_was_given(self) -> None:
        # Arrange
        unreachable = replace(SETTINGS, port=UNREACHABLE_PORT)

        # Act
        engine = create_engine(unreachable, BOUNDS)

        # Assert
        self.assertEqual(POOL_SIZE, cast("QueuePool", engine.pool).size())


if __name__ == "__main__":
    unittest.main()
