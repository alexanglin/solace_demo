"""Whether the operator-invoked retirement command deletes only what it may.

``provisioning.apply`` converges by writing desired state and never deletes, which
``test_provisioning.py`` pins deliberately. Retiring a queue the desired state no longer names
is therefore a separate, operator-invoked step: ADR-0154 calls it an "operator-invoked immediate
retirement readback" and ADR-0157 permits deletion "only through ADR-0154's separately authorized
two-step retirement readback".

The two-step plan and its refusals already exist and are proved in ``test_provisioning.py``. What
is proved here is the console seam around them: that the roster the plan is computed against is
the one provisioning writes, that an operator who names no roster is refused before a socket is
opened, and that a refused pair fails closed without a traceback or a credential.

The roster guard is the sharp one. The plan is broker inventory minus desired state, so running
this with no ``--drone`` would make every per-drone command queue stale and plan its deletion.
That is an operator error rather than a converged state, and it is refused as one.
"""

from __future__ import annotations

import io
import runpy
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from aerial_rescue_broker import retire as retire_module
from aerial_rescue_broker.deployment import (
    ADMIN_CREDENTIAL,
    CERTIFICATE_AUTHORITY,
    credential_path,
)
from aerial_rescue_broker.provisioning import (
    DesiredState,
    MonitorRow,
    ProvisioningError,
    ProvisioningRefusal,
    QueueRetirementPair,
    QueueRetirementPlan,
    Request,
)
from aerial_rescue_broker.retire import main
from aerial_rescue_broker.semp import SempEndpoint
from aerial_rescue_domain.principals import Principal

VPN = "default"
NAMESPACE = "aerial-rescue-mesh"
DRONES = ("--drone", "drone-sim-01", "--drone", "drone-sim-02")
STALE = "aerial-rescue/v1/fleet-simulator/drone.command/drone-sim-09"
CREDENTIAL = "broker-admin-password"


class _UnreadTransport:
    """A transport the console must never touch, because the retirement step is injected.

    Every method raises rather than returning a benign value, so a console that quietly grew a
    read of its own fails this suite instead of passing it.
    """

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Refuse: the console performs no configuration write."""
        raise AssertionError(request)

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Refuse: the console performs no configuration read."""
        raise AssertionError(path)

    def require_config_fields(self, required: Mapping[str, frozenset[str]]) -> None:
        """Refuse: the console asserts no schema requirement of its own."""
        raise AssertionError(required)

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Refuse: the console performs no monitor read."""
        raise AssertionError(path)

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Refuse: the console performs no monitor read."""
        raise AssertionError(path)

    def read_monitor_count(self, path: str) -> int:
        """Refuse: the console performs no monitor read."""
        raise AssertionError(path)


def _plan(*primaries: str) -> QueueRetirementPlan:
    """Return a plan naming one pair per primary, as the planner would."""
    return QueueRetirementPlan(
        VPN,
        tuple(QueueRetirementPair(primary, f"{primary}_dmq") for primary in primaries),
    )


def _material(case: unittest.TestCase) -> Path:
    """Write the generated deploy directory the console reads before it reaches the broker.

    ``main`` builds its desired state and its endpoint from this directory, so a case that leaves
    the default in place reads whichever material the developer happens to have generated and
    refuses with ``MISSING_MATERIAL`` wherever ``just secrets`` has not run. The placeholders are
    never used to open a connection, because the transport is injected.
    """
    deploy = Path(case.enterContext(tempfile.TemporaryDirectory())) / "deploy"
    (deploy / "certs").mkdir(parents=True)
    (deploy / "secrets").mkdir(parents=True)
    (deploy / CERTIFICATE_AUTHORITY).write_text("placeholder authority", encoding="utf-8")
    (deploy / ADMIN_CREDENTIAL).write_text("placeholder-credential", encoding="utf-8")
    for role in Principal:
        credential_path(deploy, role).write_text(f"placeholder-{role.value}", encoding="utf-8")
    return deploy


class RetirementConsoleTests(unittest.TestCase):
    def run_console(
        self,
        arguments: Sequence[str],
        *,
        plan: QueueRetirementPlan | None = None,
        failure: ProvisioningError | None = None,
    ) -> tuple[int, str, str, list[SempEndpoint], list[QueueRetirementPlan]]:
        """Run the console with both retirement steps injected, opening no socket."""
        out, error = io.StringIO(), io.StringIO()
        opened: list[SempEndpoint] = []
        retired: list[QueueRetirementPlan] = []

        def session(target: SempEndpoint) -> _UnreadTransport:
            opened.append(target)
            return _UnreadTransport()

        def retirer(transport: object, state: DesiredState) -> QueueRetirementPlan:
            del transport, state
            if failure is not None:
                raise failure
            applied = _plan() if plan is None else plan
            retired.append(applied)
            return applied

        status = main(
            [*arguments, "--deploy-directory", str(_material(self))],
            session=session,
            retire=retirer,
            out=out,
            error=error,
        )
        return status, out.getvalue(), error.getvalue(), opened, retired

    def test_a_converged_broker_retires_nothing_and_reports_it(self) -> None:
        # Arrange
        arguments = ("--vpn", VPN, "--namespace", NAMESPACE, *DRONES)

        # Act
        status, out, error, opened, retired = self.run_console(arguments)

        # Assert
        self.assertEqual((0, "", 1), (status, error, len(opened)))
        self.assertEqual([_plan()], retired)
        self.assertIn("0", out)

    def test_every_planned_pair_is_retired_and_counted(self) -> None:
        # Arrange
        planned = _plan(STALE, f"{STALE}-b")

        # Act
        status, out, error, _, retired = self.run_console(
            ("--vpn", VPN, "--namespace", NAMESPACE, *DRONES), plan=planned
        )

        # Assert
        self.assertEqual((0, "", [planned]), (status, error, retired))
        self.assertIn("2", out)

    def test_an_undeclared_drone_roster_is_refused_before_a_transport_is_opened(self) -> None:
        # Arrange
        arguments = ("--vpn", VPN, "--namespace", NAMESPACE)

        # Act
        status, out, error, opened, retired = self.run_console(arguments)

        # Assert
        self.assertEqual((1, "", [], []), (status, out, opened, retired))
        self.assertTrue(error.startswith("FAILED:"), error)

    def test_an_alternate_namespace_is_refused_before_a_transport_is_opened(self) -> None:
        # Arrange
        arguments = ("--vpn", VPN, "--namespace", "someone-elses-mesh", *DRONES)

        # Act
        status, _, error, opened, retired = self.run_console(arguments)

        # Assert
        self.assertEqual((1, [], []), (status, opened, retired))
        self.assertIn(NAMESPACE, error)

    def test_a_refused_retirement_reports_one_without_a_traceback_or_a_credential(self) -> None:
        # Arrange
        refusal = ProvisioningError(ProvisioningRefusal.UNSAFE_RETIREMENT, STALE)

        # Act
        status, out, error, _, retired = self.run_console(
            ("--vpn", VPN, "--namespace", NAMESPACE, *DRONES),
            plan=_plan(STALE),
            failure=refusal,
        )

        # Assert
        self.assertEqual((1, "", []), (status, out, retired))
        self.assertTrue(error.startswith("FAILED:"), error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn(CREDENTIAL, error)


class RetirementInvocationTests(unittest.TestCase):
    def test_running_the_module_reaches_the_console_and_fails_closed(self) -> None:
        """A module without an entry guard imports, exits zero, and retires nothing silently."""
        # Arrange
        argv = ["aerial_rescue_broker.retire", "--namespace", "someone-elses-mesh"]
        out, error = io.StringIO(), io.StringIO()

        # Act
        with (
            patch.object(sys, "argv", argv),
            redirect_stdout(out),
            redirect_stderr(error),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(retire_module.__file__), run_name="__main__")

        # Assert
        self.assertEqual(1, raised.value.code)
        self.assertTrue(error.getvalue().startswith("FAILED:"), error.getvalue())
        self.assertEqual("", out.getvalue())

    def test_an_unknown_flag_is_refused_rather_than_ignored(self) -> None:
        # Arrange
        arguments = ["--vpn", VPN, "--namespace", NAMESPACE, "--force"]

        # Act
        with pytest.raises(SystemExit) as raised:
            main(arguments, out=io.StringIO(), error=io.StringIO())

        # Assert
        self.assertEqual(2, raised.value.code)


if __name__ == "__main__":
    unittest.main()
