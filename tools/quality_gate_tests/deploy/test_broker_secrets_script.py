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
    "secrets/session-secret-key",
    "secrets/scenario-control-secret",
    "secrets/fleet-control-secret",
)
UNUSED_SEMP_DISCOVERY_PATH = "secrets/semp-discovery-password"
"""The optional external SEMP consumer has no repository-provisioned identity."""
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


ROLE_ENVIRONMENT = "secrets/.env.roles"
"""The generated file Compose reads for the ten role identities; never tracked."""
SESSION_VARIABLE = "SESSION_SECRET_KEY"
"""The Web UI's session signing key. The image ships a placeholder and the upstream check is
presence-only, so an unreplaced value signs real sessions (ADR-0102)."""


def _variable(role: Principal, suffix: str) -> str:
    """Return the compose variable carrying ``role``'s username or password."""
    return f"SOLACE_{role.value.replace('-', '_').upper()}_{suffix}"


def _role_environment(deploy: Path) -> dict[str, str]:
    """Return every assignment in the generated role environment file."""
    pairs = (
        line.partition("=")
        for line in (deploy / ROLE_ENVIRONMENT).read_text(encoding="utf-8").splitlines()
        if line
    )
    return {key: value for key, separator, value in pairs if separator}


def _suffixed(declarations: dict[str, str], suffix: str) -> dict[str, str]:
    """Return only the declarations whose name ends with ``suffix``."""
    return {name: value for name, value in declarations.items() if name.endswith(suffix)}


def _expected_passwords(deploy: Path) -> dict[str, str]:
    """Return the password each role's generated credential file holds, keyed by variable."""
    passwords: dict[str, str] = {}
    for role in Principal:
        credential = deploy / f"secrets/broker-{role.value}-password"
        passwords[_variable(role, "PASSWORD")] = credential.read_text(encoding="utf-8")
    return passwords


class BrokerSecretsScriptTests(QualityGateTestCase):
    def generate(
        self,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the script inside ``repository`` against its ``deploy/`` directory."""
        return self.run_script(SCRIPT, repository, arguments, environment)

    def test_it_creates_the_authority_certificate_server_pem_and_fifteen_used_passwords(
        self,
    ) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(15, len((*STACK_PASSWORDS, *ROLE_PASSWORDS)))
        self.assertTrue(all((repository / "deploy" / name).is_file() for name in PRIVATE_FILES))
        self.assertTrue((repository / "deploy" / "certs" / "ca.pem").is_file())
        self.assertFalse((repository / "deploy" / UNUSED_SEMP_DISCOVERY_PATH).exists())
        self.assertNotIn("semp-discovery", result.stdout)

    def test_private_control_hops_receive_distinct_256_bit_secrets(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)
        values = {
            name: (repository / "deploy" / name).read_text(encoding="ascii").strip()
            for name in (
                "secrets/scenario-control-secret",
                "secrets/fleet-control-secret",
            )
        }

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({PASSWORD_HEX_LENGTH}, {len(value) for value in values.values()})
        self.assertEqual(2, len(set(values.values())))

    def test_it_generates_the_scenario_service_credential_and_environment_pair(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        credential_name = "secrets/broker-scenario-service-password"

        # Act
        result = self.generate(repository)
        declarations = _role_environment(repository / "deploy")

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((repository / "deploy" / credential_name).is_file())
        self.assertEqual(
            "scenario-service",
            declarations.get("SOLACE_SCENARIO_SERVICE_USERNAME"),
        )
        self.assertEqual(
            (repository / "deploy" / credential_name).read_text(encoding="utf-8"),
            declarations.get("SOLACE_SCENARIO_SERVICE_PASSWORD"),
        )

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

    def test_it_writes_the_role_environment_file_compose_reads(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.generate(repository)

        # Assert
        declarations = _role_environment(repository / "deploy")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {_variable(role, "USERNAME"): role.value for role in Principal},
            _suffixed(declarations, "_USERNAME"),
        )

    def test_the_role_environment_file_is_private(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        mode = (repository / "deploy" / ROLE_ENVIRONMENT).stat().st_mode
        self.assertEqual(0o600, stat.S_IMODE(mode))

    def test_the_role_environment_file_carries_each_roles_generated_password(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        deploy = repository / "deploy"
        self.assertEqual(
            _expected_passwords(deploy),
            _suffixed(_role_environment(deploy), "_PASSWORD"),
        )

    def test_the_role_environment_file_carries_a_generated_session_secret(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        self.generate(repository)

        # Assert
        secret = _role_environment(repository / "deploy").get(SESSION_VARIABLE, "")
        self.assertEqual(PASSWORD_HEX_LENGTH, len(secret))
        self.assertTrue(all(character in "0123456789abcdef" for character in secret))

    def test_a_deleted_role_environment_file_is_rewritten_without_rotating_anything(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        self.generate(repository)
        before = _material(repository / "deploy")
        (repository / "deploy" / ROLE_ENVIRONMENT).unlink()

        # Act
        result = self.generate(repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, _material(repository / "deploy"))
        self.assertTrue((repository / "deploy" / ROLE_ENVIRONMENT).is_file())

    def test_rotating_rewrites_the_role_environment_file_with_the_new_passwords(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        self.generate(repository)
        before = _role_environment(repository / "deploy")

        # Act
        self.generate(repository, ("--rotate",))

        # Assert
        deploy = repository / "deploy"
        after = _role_environment(deploy)
        self.assertNotEqual(before, after)
        self.assertEqual(_expected_passwords(deploy), _suffixed(after, "_PASSWORD"))


if __name__ == "__main__":
    unittest.main()
