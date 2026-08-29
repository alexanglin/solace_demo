"""Where the durable cluster is, who connects to it, and what never leaves this module.

``deploy/compose.yaml`` names the user and the database, ``scripts/broker-secrets.sh`` writes
the credential, and ADR-0003 fixes the driver. This module resolves the three into one value
and keeps the credential out of the data source name *by construction* rather than by
escaping it: the credential is a member of the settings value and never a member of the URL,
so nothing downstream can reintroduce it by formatting a string.

Every refusal is asserted by its structured reason and the offending value, and one test
sweeps every refusal this module can raise to prove none of them exposes the credential.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_store.settings import (
    CONTAINER_HOST,
    CREDENTIAL_FILE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    POSTGRES_DB_SETTING,
    POSTGRES_USER_SETTING,
    REDACTED,
    DatabaseResolver,
    DatabaseSettings,
    SettingsError,
    SettingsRefusal,
    data_source_name,
    database_settings,
    read_credential,
)

CREDENTIAL: Final = "fixture-not-a-real-credential"
USER: Final = "aerial_rescue"
DATABASE: Final = "aerial_rescue"
ENVIRONMENT: Final[Mapping[str, str]] = {
    POSTGRES_USER_SETTING: USER,
    POSTGRES_DB_SETTING: DATABASE,
}
SETTINGS: Final = DatabaseSettings(
    host=DEFAULT_HOST, port=DEFAULT_PORT, user=USER, database=DATABASE, password=CREDENTIAL
)


def _deploy(case: unittest.TestCase, *, complete: bool = True) -> Path:
    """Write the deploy directory the generator produces, optionally missing the credential."""
    deploy = Path(case.enterContext(tempfile.TemporaryDirectory())) / "deploy"
    (deploy / "secrets").mkdir(parents=True)
    if complete:
        (deploy / CREDENTIAL_FILE).write_text(f"{CREDENTIAL}\n", encoding="utf-8")
    return deploy


def _refusal(environment: Mapping[str, str], deploy: Path) -> SettingsError | None:
    """Return the refusal these inputs produce, or None when they are accepted."""
    try:
        database_settings(environment, deploy)
    except SettingsError as error:
        return error
    return None


def _exposed(error: SettingsError | None) -> str:
    """Return everything a refusal exposes, or the empty string when there was none."""
    return "" if error is None else f"{error} {error.refusal!r} {error.value!r}"


class DataSourceNameTests(unittest.TestCase):
    def test_the_data_source_name_names_the_async_driver_and_carries_no_credential(self) -> None:
        # Arrange
        settings = SETTINGS

        # Act
        name = data_source_name(settings)

        # Assert
        self.assertEqual(
            ("postgresql+asyncpg://aerial_rescue@127.0.0.1:5432/aerial_rescue", False),
            (name, CREDENTIAL in name),
        )

    def test_the_data_source_name_addresses_the_user_host_port_and_database_it_was_given(
        self,
    ) -> None:
        # Arrange
        settings = DatabaseSettings(
            host=CONTAINER_HOST, port=6543, user="probe", database="probe_db", password=CREDENTIAL
        )

        # Act
        name = data_source_name(settings)

        # Assert
        self.assertEqual("postgresql+asyncpg://probe@postgres:6543/probe_db", name)


class ReadCredentialTests(unittest.TestCase):
    def test_the_credential_is_read_from_the_generated_file_and_stripped(self) -> None:
        # Arrange
        deploy = _deploy(self)

        # Act
        credential = read_credential(deploy)

        # Assert
        self.assertEqual(CREDENTIAL, credential)

    def test_a_credential_file_the_generator_has_not_written_fails_closed_naming_the_path(
        self,
    ) -> None:
        # Arrange
        deploy = _deploy(self, complete=False)

        # Act
        with pytest.raises(SettingsError) as captured:
            read_credential(deploy)

        # Assert
        self.assertEqual(
            (SettingsRefusal.MISSING_MATERIAL, str(deploy / CREDENTIAL_FILE)),
            (captured.value.refusal, captured.value.value),
        )


class DatabaseSettingsTests(unittest.TestCase):
    def test_the_public_resolver_protocol_matches_the_canonical_resolver(self) -> None:
        # Arrange
        deploy = _deploy(self)
        resolver: DatabaseResolver = database_settings

        # Act
        settings = resolver(ENVIRONMENT, deploy, host=CONTAINER_HOST)

        # Assert
        self.assertEqual(CONTAINER_HOST, settings.host)

    def test_the_environment_and_the_generated_file_supply_the_whole_target(self) -> None:
        # Arrange
        deploy = _deploy(self)

        # Act
        settings = database_settings(ENVIRONMENT, deploy)

        # Assert
        self.assertEqual(SETTINGS, settings)

    def test_the_host_and_the_port_are_injected_over_the_loopback_default(self) -> None:
        # Arrange
        deploy = _deploy(self)

        # Act
        settings = database_settings(ENVIRONMENT, deploy, host=CONTAINER_HOST, port=6543)

        # Assert
        self.assertEqual((CONTAINER_HOST, 6543), (settings.host, settings.port))

    def test_a_blank_setting_fails_closed_with_the_missing_setting_refusal(self) -> None:
        # Arrange
        environment = {POSTGRES_USER_SETTING: "  ", POSTGRES_DB_SETTING: DATABASE}

        # Act
        with pytest.raises(SettingsError) as captured:
            database_settings(environment, _deploy(self))

        # Assert
        self.assertEqual(
            (SettingsRefusal.MISSING_SETTING, POSTGRES_USER_SETTING),
            (captured.value.refusal, captured.value.value),
        )

    def test_every_required_setting_is_refused_by_its_own_name(self) -> None:
        # Arrange
        names = (POSTGRES_USER_SETTING, POSTGRES_DB_SETTING)
        deploy = _deploy(self)
        partial = tuple(
            {key: value for key, value in ENVIRONMENT.items() if key != absent} for absent in names
        )

        # Act
        refused = tuple(_refusal(environment, deploy) for environment in partial)

        # Assert
        self.assertEqual(names, tuple(None if error is None else error.value for error in refused))


class RedactionTests(unittest.TestCase):
    def test_the_settings_representation_redacts_the_credential(self) -> None:
        # Arrange
        settings = SETTINGS

        # Act
        rendered = repr(settings)

        # Assert
        self.assertEqual(
            (False, True, True, True),
            (
                CREDENTIAL in rendered,
                REDACTED in rendered,
                DATABASE in rendered,
                DEFAULT_HOST in rendered,
            ),
        )

    def test_no_refusal_this_module_raises_exposes_the_credential(self) -> None:
        # Arrange
        complete = _deploy(self)
        cases: tuple[tuple[Mapping[str, str], Path], ...] = (
            ({}, complete),
            ({POSTGRES_USER_SETTING: USER}, complete),
            (ENVIRONMENT, _deploy(self, complete=False)),
        )

        # Act
        exposed = tuple(_exposed(_refusal(environment, deploy)) for environment, deploy in cases)

        # Assert
        self.assertEqual(
            (len(cases), 0, 0),
            (
                len(exposed),
                sum(1 for text in exposed if not text),
                sum(1 for text in exposed if CREDENTIAL in text),
            ),
        )


if __name__ == "__main__":
    unittest.main()
