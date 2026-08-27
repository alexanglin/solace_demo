"""Live PubSub+ probes retain the transport and dead-message policies they attest to.

The live modules are resourceful evidence and do not run in deterministic stages.  Their source is
still policy-bearing: a probe that ignores certificate expiry or reads the broker factory DMQ can be
green while the application runtime violates the stronger accepted Solace boundary.  This offline
check keeps those two false-green regressions visible without opening a socket.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TEST_ROOT = REPOSITORY_ROOT / "tests"
DMQ_PROBES = (
    Path("tests/integration/test_guaranteed_delivery_live.py"),
    Path("tests/integration/test_command_dispatch_live.py"),
    Path("tests/integration/test_backlog_recovery_live.py"),
)


def _tree(path: Path) -> ast.Module:
    """Parse one project-owned test module as deterministic policy input."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())


def _is_false(node: ast.expr) -> bool:
    """Return whether ``node`` is the literal false value required by the SDK argument."""
    return isinstance(node, ast.Constant) and node.value is False


def _is_true(node: ast.expr) -> bool:
    """Return whether ``node`` is the literal true value required for hostname validation."""
    return isinstance(node, ast.Constant) and node.value is True


def _certificate_validation_calls() -> tuple[tuple[Path, ast.Call], ...]:
    """Return every native Solace certificate-validation call under the root live-test tree."""
    calls: list[tuple[Path, ast.Call]] = []
    for path in sorted(TEST_ROOT.rglob("*.py")):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "with_certificate_validation"
            ):
                calls.append((path.relative_to(REPOSITORY_ROOT), node))
    return tuple(calls)


def _certificate_policy_violations() -> tuple[int, tuple[str, ...]]:
    """Return the call count and every call that weakens expiry or hostname validation."""
    calls = _certificate_validation_calls()
    violations: list[str] = []
    for path, call in calls:
        validates_hostname = any(
            keyword.arg == "validate_server_name" and _is_true(keyword.value)
            for keyword in call.keywords
        )
        if not call.args or not _is_false(call.args[0]) or not validates_hostname:
            violations.append(f"{path.as_posix()}:{call.lineno}")
    return len(calls), tuple(violations)


def _dmq_policy() -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Return probes deriving an isolated DMQ and any factory-DMQ references."""
    derived: list[Path] = []
    factory_references: list[str] = []
    for relative in DMQ_PROBES:
        tree = _tree(REPOSITORY_ROOT / relative)
        nodes = tuple(ast.walk(tree))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dead_message_queue_name"
            for node in nodes
        ):
            derived.append(relative)
        factory_references.extend(
            f"{relative.as_posix()}:{node.lineno}"
            for node in nodes
            if isinstance(node, ast.Name) and node.id == "DEAD_MESSAGE_QUEUE"
        )
    return tuple(derived), tuple(sorted(factory_references))


class LiveBrokerProbePolicyTests(QualityGateTestCase):
    def test_native_live_clients_validate_certificate_expiry_and_hostname(self) -> None:
        # Arrange
        minimum_call_count = 1

        # Act
        call_count, violations = _certificate_policy_violations()

        # Assert
        self.assertGreaterEqual(call_count, minimum_call_count)
        self.assertEqual((), violations)

    def test_dead_message_probes_derive_their_source_queue_s_isolated_dmq(self) -> None:
        # Arrange
        expected = DMQ_PROBES

        # Act
        derived, factory_references = _dmq_policy()

        # Assert
        self.assertEqual((expected, ()), (derived, factory_references))


if __name__ == "__main__":
    import unittest

    unittest.main()
