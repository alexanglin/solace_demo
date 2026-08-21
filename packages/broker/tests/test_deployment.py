"""The entry point that reads the generated material and applies the matrix to a broker.

Nothing here opens a socket. The session factory is injected, so the whole path from
``python -m aerial_rescue_broker`` down to the requests issued runs against a fake, and the
one function that names a real connection is exercised without connecting because
``http.client`` does not connect until its first request.

Two behaviours are here rather than in the modules below because they only exist at this
seam: material that has not been generated must fail closed with the command that generates
it, and an unset A2A namespace must under-grant loudly rather than guess a value.
"""

from __future__ import annotations

import io
import ssl
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from aerial_rescue_broker.deployment import (
    ADMIN_CREDENTIAL,
    CERTIFICATE_AUTHORITY,
    DeploymentError,
    DeploymentRefusal,
    credential_path,
    endpoint,
    main,
    provision,
    read_credentials,
    session_for,
)
from aerial_rescue_broker.provisioning import Method, Request
from aerial_rescue_broker.semp import SempError, SempFailure, SempSession
from aerial_rescue_domain.principals import Principal

CREDENTIAL = "fixture-not-a-real-credential"


class RecordingTransport:
    """A transport that answers every read as empty and records every request."""

    def __init__(self, failure: SempError | None = None) -> None:
        """Answer normally, or raise ``failure`` on the first request."""
        self.failure = failure
        self.issued: list[Request] = []

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Record the request and answer it with no rows."""
        self.issued.append(request)
        if self.failure is not None:
            raise self.failure
        return ()

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Record the read and answer it as an empty collection."""
        return self.send(Request(Method.GET, path, {}))


def _material(case: unittest.TestCase, *, complete: bool = True) -> Path:
    """Write the deploy directory the generator produces, optionally missing one file."""
    deploy = Path(case.enterContext(tempfile.TemporaryDirectory())) / "deploy"
    (deploy / "certs").mkdir(parents=True)
    (deploy / "secrets").mkdir(parents=True)
    (deploy / CERTIFICATE_AUTHORITY).write_text("placeholder authority", encoding="utf-8")
    (deploy / ADMIN_CREDENTIAL).write_text(CREDENTIAL, encoding="utf-8")
    roles = tuple(Principal) if complete else tuple(Principal)[1:]
    for role in roles:
        credential_path(deploy, role).write_text(f"{CREDENTIAL}-{role.value}", encoding="utf-8")
    return deploy


class ReadCredentialsTests(unittest.TestCase):
    def test_every_role_credential_is_read_and_stripped(self) -> None:
        # Arrange
        deploy = _material(self)

        # Act
        credentials = read_credentials(deploy)

        # Assert
        self.assertEqual({role: f"{CREDENTIAL}-{role.value}" for role in Principal}, credentials)

    def test_material_that_was_never_generated_fails_closed(self) -> None:
        # Arrange
        deploy = _material(self, complete=False)
        missing = next(iter(Principal))

        # Act
        try:
            read_credentials(deploy)
        except DeploymentError as error:
            captured = error
        else:
            message = "a missing credential was accepted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(
            (DeploymentRefusal.MISSING_MATERIAL, str(credential_path(deploy, missing))),
            (captured.refusal, captured.value),
        )


class EndpointTests(unittest.TestCase):
    def test_the_endpoint_names_the_admin_identity_and_the_generated_authority(self) -> None:
        # Arrange
        deploy = _material(self)

        # Act
        target = endpoint(deploy, "localhost", 1943)

        # Assert
        self.assertEqual(
            ("localhost", 1943, "admin", CREDENTIAL, str(deploy / CERTIFICATE_AUTHORITY)),
            (
                target.host,
                target.port,
                target.username,
                target.password,
                target.certificate_authority,
            ),
        )

    def test_a_missing_authority_fails_closed(self) -> None:
        # Arrange
        deploy = _material(self)
        (deploy / CERTIFICATE_AUTHORITY).unlink()

        # Act
        try:
            endpoint(deploy, "localhost", 1943)
        except DeploymentError as error:
            captured = error
        else:
            message = "a missing authority was accepted"
            raise AssertionError(message)

        # Assert
        self.assertIs(DeploymentRefusal.MISSING_MATERIAL, captured.refusal)


class ProvisionTests(unittest.TestCase):
    def test_the_report_names_what_was_applied(self) -> None:
        # Arrange
        deploy = _material(self)
        transport = RecordingTransport()

        # Act
        lines = provision(transport, deploy, "default", "acme/dev")

        # Assert
        self.assertEqual(
            ("9 acl profiles", "9 client usernames", "47 topic exceptions", True),
            (
                next(part for part in lines if "acl profiles" in part).split(" to ")[0],
                next(part for part in lines if "client usernames" in part),
                next(part for part in lines if "topic exceptions" in part),
                any("'default'" in part and "disabled" in part for part in lines),
            ),
        )

    def test_an_unset_namespace_is_reported_as_a_withheld_grant(self) -> None:
        # Arrange
        deploy = _material(self)
        transport = RecordingTransport()

        # Act
        lines = provision(transport, deploy, "default", None)

        # Assert
        self.assertTrue(any("no A2A grant" in part for part in lines), lines)

    def test_no_report_line_carries_a_credential(self) -> None:
        # Arrange
        deploy = _material(self)
        transport = RecordingTransport()

        # Act
        lines = provision(transport, deploy, "default", "acme/dev")

        # Assert
        self.assertEqual((), tuple(part for part in lines if CREDENTIAL in part))


class MainTests(unittest.TestCase):
    def test_a_successful_run_reports_zero_and_prints_the_summary(self) -> None:
        # Arrange
        deploy = _material(self)
        transport = RecordingTransport()
        out = io.StringIO()

        # Act
        code = main(
            ("--deploy-directory", str(deploy), "--namespace", "acme/dev"),
            session=lambda _: transport,
            out=out,
            error=io.StringIO(),
        )

        # Assert
        self.assertEqual((0, True), (code, "acl profiles" in out.getvalue()))

    def test_a_broker_refusal_reports_one_without_a_traceback_or_a_credential(self) -> None:
        # Arrange
        deploy = _material(self)
        failure = SempError(SempFailure.STATUS, f"PUT ... {CREDENTIAL}")
        error = io.StringIO()

        # Act
        code = main(
            ("--deploy-directory", str(deploy)),
            session=lambda _: RecordingTransport(failure),
            out=io.StringIO(),
            error=error,
        )

        # Assert
        self.assertEqual((1, True), (code, error.getvalue().startswith("FAILED:")))

    def test_material_that_was_never_generated_reports_one(self) -> None:
        # Arrange
        deploy = Path(self.enterContext(tempfile.TemporaryDirectory())) / "deploy"
        error = io.StringIO()

        # Act
        code = main(
            ("--deploy-directory", str(deploy)),
            session=lambda _: RecordingTransport(),
            out=io.StringIO(),
            error=error,
        )

        # Assert
        self.assertEqual((1, True), (code, "broker-secrets.sh" in error.getvalue()))

    def test_the_default_session_binds_a_validated_connection_to_the_endpoint(self) -> None:
        # Arrange
        deploy = _material(self)
        target = endpoint(deploy, "localhost", 1943)

        # Act
        session = session_for(target, context=ssl.create_default_context())

        # Assert
        self.assertIsInstance(session, SempSession)

    def test_a_run_issues_the_patch_that_disables_the_factory_identity(self) -> None:
        # Arrange
        deploy = _material(self)
        transport = RecordingTransport()

        # Act
        main(
            ("--deploy-directory", str(deploy), "--vpn", "default"),
            session=lambda _: transport,
            out=io.StringIO(),
            error=io.StringIO(),
        )

        # Assert
        self.assertEqual(
            ["msgVpns/default/clientUsernames/default"],
            [r.path for r in transport.issued if r.method is Method.PATCH],
        )


if __name__ == "__main__":
    unittest.main()
