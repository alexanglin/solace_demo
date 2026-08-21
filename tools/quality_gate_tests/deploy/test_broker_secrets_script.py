"""Tests for the script that generates the per-checkout certificate authority and secrets."""

from __future__ import annotations

import stat
import subprocess
import unittest
from pathlib import Path

from aerial_rescue_domain.principals import Principal

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCRIPT = REPOSITORY_ROOT / "scripts" / "broker-secrets.sh"
PASSWORD_HEX_LENGTH = 64
"""32 random bytes rendered as hexadecimal."""
STACK_PASSWORDS = (
    "secrets/broker-admin-password",
    "secrets/postgres-password",
    "secrets/semp-discovery-password",
)
ROLE_PASSWORDS = tuple(f"secrets/broker-{role.value}-password" for role in Principal)
"""One per broker authorization role (ADR-0061); the script's own list is held equal below."""
PRIVATE_FILES = (
    "secrets/ca.key",
    "secrets/broker-server.key",
    "secrets/broker-server.crt",
    "secrets/broker-server.pem",
    *STACK_PASSWORDS,
    *ROLE_PASSWORDS,
)


def _material(deploy: Path) -> dict[str, bytes]:
    """Return every generated file's bytes keyed by its path under ``deploy``."""
    return {name: (deploy / name).read_bytes() for name in (*PRIVATE_FILES, "certs/ca.pem")}


class BrokerSecretsScriptTests(QualityGateTestCase):
    def generate(
        self,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the script inside ``repository`` against its ``deploy/`` directory."""
        return self.run_script(SCRIPT, repository, arguments, environment)

    def test_it_creates_the_authority_certificate_server_pem_and_twelve_passwords(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(all((repository / "deploy" / name).is_file() for name in PRIVATE_FILES))
        self.assertTrue((repository / "deploy" / "certs" / "ca.pem").is_file())

    def test_private_files_are_mode_0600_and_the_public_certificate_is_readable(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        modes = {
            name: stat.S_IMODE((repository / "deploy" / name).stat().st_mode)
            for name in PRIVATE_FILES
        }
        self.assertEqual({name: 0o600 for name in PRIVATE_FILES}, modes)
        public = stat.S_IMODE((repository / "deploy" / "certs" / "ca.pem").stat().st_mode)
        self.assertEqual(0o644, public)

    def test_the_server_pem_holds_the_key_before_the_certificate(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        pem = (repository / "deploy" / "secrets" / "broker-server.pem").read_text(encoding="utf-8")
        self.assertLess(pem.index("PRIVATE KEY-----"), pem.index("BEGIN CERTIFICATE-----"))

    def test_the_server_certificate_carries_the_three_subject_alternative_names(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)

        # Assert
        self.assertIn("DNS:localhost", result.stdout)
        self.assertIn("DNS:broker", result.stdout)
        self.assertIn("IP Address:127.0.0.1", result.stdout)

    def test_stdout_never_contains_key_material_or_a_password(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)

        # Assert
        passwords = [
            (repository / "deploy" / name).read_text(encoding="utf-8").strip()
            for name in (*STACK_PASSWORDS, *ROLE_PASSWORDS)
        ]
        self.assertNotIn("PRIVATE KEY", result.stdout + result.stderr)
        self.assertTrue(
            all(password not in result.stdout + result.stderr for password in passwords)
        )
        self.assertTrue(all(len(password) == PASSWORD_HEX_LENGTH for password in passwords))

    def test_a_second_run_changes_nothing(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        self.generate(repository)
        before = _material(repository / "deploy")

        # Act
        result = self.generate(repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("unchanged", result.stdout)
        self.assertEqual(before, _material(repository / "deploy"))

    def test_rotate_replaces_the_material(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        self.generate(repository)
        before = _material(repository / "deploy")

        # Act
        result = self.generate(repository, ("--rotate",))

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        after = _material(repository / "deploy")
        self.assertTrue(all(after[name] != before[name] for name in before))

    def test_a_missing_openssl_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        # An empty directory, not a real one: Debian ships openssl in /bin, so naming a
        # system directory here would leave the executable on PATH and the test would
        # assert nothing on Linux while passing on macOS.
        empty_path = self.temporary_directory()

        # Act
        result = self.generate(repository, environment={"PATH": str(empty_path)})

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: openssl", result.stderr)
        self.assertFalse((repository / "deploy").exists())

    def test_a_missing_password_is_filled_without_rotating_the_authority(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        self.generate(repository)
        before = _material(repository / "deploy")
        (repository / "deploy" / ROLE_PASSWORDS[0]).unlink()

        # Act
        result = self.generate(repository)

        # Assert
        after = _material(repository / "deploy")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {name: before[name] for name in before if name != ROLE_PASSWORDS[0]},
            {name: after[name] for name in after if name != ROLE_PASSWORDS[0]},
        )
        self.assertNotEqual(before[ROLE_PASSWORDS[0]], after[ROLE_PASSWORDS[0]])

    def test_the_scripts_role_list_equals_the_authorization_roles(self) -> None:
        # Arrange
        declaration = SCRIPT.read_text(encoding="utf-8").partition('broker_roles="')[2]

        # Act
        listed = tuple(declaration.partition('"')[0].split())

        # Assert
        self.assertEqual(tuple(role.value for role in Principal), listed)

    def test_every_role_receives_its_own_distinct_credential(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        credentials = {
            (repository / "deploy" / name).read_text(encoding="utf-8") for name in ROLE_PASSWORDS
        }
        self.assertEqual(len(ROLE_PASSWORDS), len(credentials))

    def test_an_unknown_argument_is_refused(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository, ("--force",))

        # Assert
        self.assertEqual(2, result.returncode)
        self.assertIn("usage:", result.stderr)
        self.assertFalse((repository / "deploy").exists())


if __name__ == "__main__":
    unittest.main()
