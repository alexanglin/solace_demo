"""Whether ``just up`` starts the stack in the order the authorization matrix requires.

The Agent Mesh joined the default profile, recorded in
[ADR-0102](../../../docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md), so ``just up``
now starts it on every run. That makes the recipe's order load-bearing rather than cosmetic.

``just provision`` writes the nine least-privilege identities and disables the factory ``default``
client username, which ships enabled on an allow-everything profile
([ADR-0061](../../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md)).
A mesh that connects before it runs comes up **healthy, on factory authority** -- a failure that
looks like success. So the broker must be healthy and provisioned before the mesh is started, and
``--namespace`` must be passed, because omitting it grants the Agent Mesh roles no A2A subscription
at all.

The recipe text is the subject because the ordering lives nowhere else: Compose expresses
``depends_on``, not "provision in between".
"""

from __future__ import annotations

import json
import re
import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

JUSTFILE = REPOSITORY_ROOT / "justfile"
TEMPLATE = REPOSITORY_ROOT / ".env.example"
BROKER_SERVICE = "broker"
DATABASE_SERVICE = "postgres"
PROVISION_MODULE = "aerial_rescue_broker"
PREFLIGHT = "scripts/preflight-ollama.sh"
PLATFORM_DATABASE = "scripts/create-platform-database.sh"
NAMESPACE_FLAG = "--namespace"
SCENARIO = REPOSITORY_ROOT / "scenarios" / "v1" / "wilderness-missing-person.r1.json"
REFERENCE_DRONE_ARGUMENTS = "reference_drone_arguments"


def _recipe(name: str) -> list[str]:
    """Return the indented body lines of one just recipe, comments and blanks removed."""
    lines = JUSTFILE.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if re.match(rf"^{re.escape(name)}(\s+\*?[A-Z]+)?:\s*$", line)
    )
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            body.append(stripped)
    return body


def _declared_namespace() -> str:
    """Return the A2A namespace the environment template fixes."""
    pairs = (
        line.partition("=")
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, separator, value in pairs if separator}["NAMESPACE"]


def _just_string(name: str) -> str:
    """Return one quoted top-level Just string assignment."""
    pattern = re.compile(rf'^{re.escape(name)}\s*:=\s*"([^"]*)"$', re.MULTILINE)
    match = pattern.search(JUSTFILE.read_text(encoding="utf-8"))
    if match is None:
        message = f"missing Just assignment: {name}"
        raise AssertionError(message)
    return match.group(1)


class StartupRecipeTests(QualityGateTestCase):
    def test_the_broker_and_database_come_up_and_are_waited_for_first(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        first = body[0]

        # Assert
        self.assertIn("--wait", first)
        self.assertTrue(first.rstrip().endswith(f"{BROKER_SERVICE} {DATABASE_SERVICE}"), first)

    def test_the_authorization_matrix_is_applied_before_the_mesh_is_started(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        provision = next(index for index, line in enumerate(body) if PROVISION_MODULE in line)

        # Assert
        started = [index for index, line in enumerate(body) if "up --detach" in line]
        self.assertEqual([0, len(body) - 1], started)
        self.assertLess(provision, started[-1])

    def test_provisioning_states_the_namespace_the_template_fixes(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        provision = next(line for line in body if PROVISION_MODULE in line)

        # Assert
        self.assertIn(f"{NAMESPACE_FLAG} {_declared_namespace()}", provision)

    def test_provisioning_names_every_twenty_plus_three_command_queue_owner(self) -> None:
        # Arrange
        body = _recipe("up")
        scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
        expected = tuple(member["identifier"] for member in scenario["members"])

        # Act
        provision = next(line for line in body if PROVISION_MODULE in line)
        declared = tuple(
            re.findall(r"--drone ([a-z0-9-]+)", _just_string(REFERENCE_DRONE_ARGUMENTS))
        )

        # Assert
        self.assertEqual(expected, declared)
        self.assertIn(f"{{{{{REFERENCE_DRONE_ARGUMENTS}}}}}", provision)

    def test_the_ollama_preflight_runs_before_the_mesh_is_started(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        preflight = next(index for index, line in enumerate(body) if PREFLIGHT in line)

        # Assert
        self.assertLess(preflight, len(body) - 1)

    def test_the_platform_database_is_created_after_the_database_and_before_the_mesh(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        creation = next(index for index, line in enumerate(body) if PLATFORM_DATABASE in line)

        # Assert
        started = [index for index, line in enumerate(body) if "up --detach" in line]
        self.assertLess(started[0], creation)
        self.assertLess(creation, started[-1])

    def test_compose_flags_reach_the_up_subcommand(self) -> None:
        # Arrange
        body = _recipe("up")

        # Act
        final = body[-1]

        # Assert
        self.assertIn("up --detach", final)
        self.assertLess(final.index("up --detach"), final.index("{{ARGS}}"))

    def test_the_showcase_still_starts_only_the_broker_and_database(self) -> None:
        # Arrange
        body = _recipe("showcase")

        # Act
        started = [line for line in body if "up --detach" in line]

        # Assert
        self.assertEqual(1, len(started))
        self.assertTrue(started[0].rstrip().endswith(f"{BROKER_SERVICE} {DATABASE_SERVICE}"))

    def test_mission_control_starts_the_guarded_explicit_subset_without_agent_mesh_or_ollama(
        self,
    ) -> None:
        # Arrange
        body = _recipe("mission-control-up")
        required = {
            "broker-event-monitor",
            "migration",
            "fleet-simulator",
            "scenario-service",
            "recorder",
            "replay-validator",
            "dashboard-api",
            "caddy",
        }

        # Act
        declared = set(_just_string("mission_control_services").split())
        joined = " ".join(body)

        # Assert
        self.assertEqual(required, declared)
        self.assertIn("--profile mission-control", joined)
        self.assertNotIn("agent-mesh", joined)
        self.assertNotIn(PREFLIGHT, joined)
        self.assertIn(PROVISION_MODULE, joined)
        self.assertIn(f"{{{{{REFERENCE_DRONE_ARGUMENTS}}}}}", joined)
        self.assertNotIn("--queue-projection", joined)


if __name__ == "__main__":
    unittest.main()
