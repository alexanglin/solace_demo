"""Whether the container assembles the Platform service's database URL without writing it down.

The Platform service takes one ``database_url`` string, credential included, where every other
store consumer in this repository takes a user and supplies its password separately. Compose
cannot carry that string: its policy gate refuses a URL with userinfo, and this repository is
public. So the owned entrypoint assembles it inside the container, from non-secret environment
values and the mounted ``postgres-password`` secret, and exports it before the connector reads
any configuration (docs/adr/0222).

The failures here are all shaped the same way: a value that is missing is a refusal naming the
variable, never the value, because this runs where a credential is in scope.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import pytest

import aerial_rescue_runtime_compat.__main__ as runtime_main
from aerial_rescue_runtime_compat.database import (
    CREDENTIAL_FILE_VARIABLE,
    DATABASE_URL_VARIABLE,
    NAME_VARIABLE,
    PlatformDatabaseError,
    export_platform_database_url,
)

USER = "aerial_rescue"
HOST = "postgres"
PORT = "5432"
DATABASE = "aerial_rescue_platform"
CREDENTIAL = "s3cr3t"
AWKWARD_CREDENTIAL = "p@ss/word:1#2"


class PlatformDatabaseUrlTests(unittest.TestCase):
    def environment(self, secret: Path | None, **overrides: str) -> dict[str, str]:
        """Return a complete container environment, with ``overrides`` applied last."""
        values = {
            "POSTGRES_USER": USER,
            "PLATFORM_DATABASE_HOST": HOST,
            "PLATFORM_DATABASE_PORT": PORT,
            NAME_VARIABLE: DATABASE,
            CREDENTIAL_FILE_VARIABLE: str(secret) if secret is not None else "",
        }
        values.update(overrides)
        return values

    def secret(self, content: str) -> Path:
        """Return a mounted-secret path holding ``content``."""
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "postgres-password"
        path.write_text(content, encoding="utf-8")
        return path

    def test_the_url_is_assembled_from_the_parts_and_the_mounted_secret(self) -> None:
        # Arrange
        values = self.environment(self.secret(f"{CREDENTIAL}\n"))

        # Act
        exported = export_platform_database_url(values)

        # Assert
        self.assertTrue(exported)
        self.assertEqual(
            f"postgresql+psycopg2://{USER}:{CREDENTIAL}@{HOST}:{PORT}/{DATABASE}",
            values[DATABASE_URL_VARIABLE],
        )

    def test_a_password_carrying_url_syntax_is_percent_encoded(self) -> None:
        # Arrange
        values = self.environment(self.secret(AWKWARD_CREDENTIAL))

        # Act
        export_platform_database_url(values)

        # Assert
        self.assertIn("p%40ss%2Fword%3A1%232", values[DATABASE_URL_VARIABLE])
        self.assertNotIn(AWKWARD_CREDENTIAL, values[DATABASE_URL_VARIABLE])

    def test_an_absent_database_name_leaves_the_environment_untouched(self) -> None:
        # Arrange
        values = self.environment(self.secret(CREDENTIAL), **{NAME_VARIABLE: ""})

        # Act
        exported = export_platform_database_url(values)

        # Assert
        self.assertFalse(exported)
        self.assertNotIn(DATABASE_URL_VARIABLE, values)

    def test_an_explicitly_supplied_url_is_not_replaced(self) -> None:
        # Arrange
        supplied = "postgresql+psycopg2://elsewhere/db"
        values = self.environment(self.secret(CREDENTIAL), **{DATABASE_URL_VARIABLE: supplied})

        # Act
        exported = export_platform_database_url(values)

        # Assert
        self.assertFalse(exported)
        self.assertEqual(supplied, values[DATABASE_URL_VARIABLE])

    def test_a_missing_or_blank_secret_is_refused_without_naming_its_content(self) -> None:
        # Arrange
        absent = self.environment(Path("/nonexistent/postgres-password"))
        blank = self.environment(self.secret("   \n"))
        cases = (absent, blank)

        # Act
        raised = []
        for values in cases:
            with pytest.raises(PlatformDatabaseError) as caught:
                export_platform_database_url(values)
            raised.append(caught.value)

        # Assert
        for error in raised:
            with self.subTest(error=error):
                self.assertIn(CREDENTIAL_FILE_VARIABLE, str(error))
                self.assertNotIn(CREDENTIAL, str(error))

    def test_a_blank_required_value_is_refused_by_its_variable_name(self) -> None:
        # Arrange
        values = self.environment(self.secret(CREDENTIAL), POSTGRES_USER="  ")

        # Act
        with pytest.raises(PlatformDatabaseError) as caught:
            export_platform_database_url(values)

        # Assert
        self.assertIn("POSTGRES_USER", str(caught.value))


class EntrypointExportTests(unittest.TestCase):
    def test_the_url_is_exported_before_any_configuration_is_read(self) -> None:
        """Loading substitutes environment references, so the export cannot follow it."""
        # Arrange
        source = inspect.getsource(runtime_main._startup)

        # Act
        positions = (
            source.index("export_platform_database_url"),
            source.index("_configuration_files"),
        )

        # Assert
        self.assertLess(positions[0], positions[1])


if __name__ == "__main__":
    unittest.main()
