"""Whether the Platform service's database is created before the mesh that migrates it.

The Agent Mesh's Platform service owns the registered model configurations, and it runs its own
Alembic history whose version table carries the default name. Two histories with the same version
table cannot share a database, so the Platform service is given its own
(``docs/adr/0222``). PostgreSQL will not create it on demand: the image initialises exactly one
database, from ``POSTGRES_DB``, and only on a first initialisation of an empty data volume, which
a running deployment has long since passed.

So the database is created by the startup recipe, before the container that would migrate it. The
script is idempotent because ``just up`` is, and it refuses a database name that is not a plain
identifier because the name reaches PostgreSQL inside a statement rather than as a parameter.

The Docker CLI is stubbed rather than mocked, so the script's real invocation path is the one under
test and no case reaches a container.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCRIPT = REPOSITORY_ROOT / "scripts" / "create-platform-database.sh"
TEMPLATE = REPOSITORY_ROOT / ".env.example"
DATABASE_KEY = "PLATFORM_DATABASE_NAME"
OWNER_KEY = "POSTGRES_USER"
CREATE_FRAGMENT = "create database"
PROBE_FRAGMENT = "pg_database"
STUB_LOG = "docker-invocations.log"

STUB = """#!/bin/sh
printf '%s\\n' "$*" >>"$PLATFORM_DATABASE_STUB_LOG"
case "$*" in
*pg_database*) printf '%s\\n' "$PLATFORM_DATABASE_STUB_EXISTS" ;;
esac
exit "${PLATFORM_DATABASE_STUB_STATUS:-0}"
"""


def _declarations() -> dict[str, str]:
    """Return every assignment in the environment template."""
    pairs = (
        line.partition("=")
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, separator, value in pairs if separator}


class PlatformDatabaseScriptTests(QualityGateTestCase):
    def create(
        self,
        *,
        exists: str = "",
        status: str = "0",
        name: str | None = None,
        docker: str | None = None,
    ) -> tuple[int, str, str, tuple[str, ...]]:
        """Run the creation script against a stubbed Docker CLI and return what it invoked."""
        repository = self.temporary_repository()
        stub = repository / "docker-stub"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
        log = repository / STUB_LOG
        environment = {
            "PLATFORM_DATABASE_DOCKER": docker if docker is not None else str(stub),
            "PLATFORM_DATABASE_STUB_LOG": str(log),
            "PLATFORM_DATABASE_STUB_EXISTS": exists,
            "PLATFORM_DATABASE_STUB_STATUS": status,
        }
        if name is not None:
            environment[DATABASE_KEY] = name
        result = self.run_script(SCRIPT, repository, (), environment)
        invocations = tuple(log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()
        return result.returncode, result.stdout, result.stderr, invocations

    def test_an_absent_database_is_created_under_the_declared_owner(self) -> None:
        # Arrange
        expected = (_declarations()[DATABASE_KEY], _declarations()[OWNER_KEY])

        # Act
        code, stdout, stderr, invocations = self.create(exists="")

        # Assert
        self.assertEqual(0, code, stderr)
        created = [line for line in invocations if CREATE_FRAGMENT in line]
        self.assertEqual(1, len(created), invocations)
        self.assertIn(expected[0], created[0])
        self.assertIn(f"-U {expected[1]}", created[0])
        self.assertIn(expected[0], stdout)

    def test_an_existing_database_is_probed_and_left_alone(self) -> None:
        # Arrange
        present = "1"

        # Act
        code, stdout, stderr, invocations = self.create(exists=present)

        # Assert
        self.assertEqual(0, code, stderr)
        self.assertEqual([], [line for line in invocations if CREATE_FRAGMENT in line])
        self.assertEqual(1, len([line for line in invocations if PROBE_FRAGMENT in line]))
        self.assertNotEqual("", stdout)

    def test_a_database_name_that_is_not_a_plain_identifier_is_refused(self) -> None:
        # Arrange
        injected = 'aerial"; drop database aerial_rescue; --'

        # Act
        code, _, stderr, invocations = self.create(name=injected)

        # Assert
        self.assertNotEqual(0, code)
        self.assertIn("REFUSED", stderr)
        self.assertEqual((), invocations)

    def test_an_unusable_docker_command_fails_closed(self) -> None:
        # Arrange
        absent = str(Path("no-such-docker-command"))

        # Act
        code, _, stderr, invocations = self.create(docker=absent)

        # Assert
        self.assertNotEqual(0, code)
        self.assertIn("MISSING", stderr)
        self.assertEqual((), invocations)

    def test_a_refused_probe_is_not_reported_as_a_created_database(self) -> None:
        # Arrange
        failing = "1"

        # Act
        code, stdout, _, _ = self.create(status=failing)

        # Assert
        self.assertNotEqual(0, code)
        self.assertNotIn(CREATE_FRAGMENT, stdout)


if __name__ == "__main__":
    unittest.main()
