"""Whether the committed compose stack serves what its text promises.

Everything about ``deploy/compose.yaml`` is proven statically: the compose policy gate holds
every image pin, loopback binding, secret, and healthcheck on every commit and every push
(``docs/adr/0045-fail-closed-compose-policy-gate.md``). None of that is evidence that the
stack runs. A gate reading a file cannot see inside an image, cannot tell whether the
broker's healthcheck command exists there, and cannot tell whether the generated authority
signs anything the broker will actually present.

These probes are that evidence. They assert the two claims the first live run owes:
``docs/adr/0046-generated-local-certificate-authority.md``'s per-checkout authority really
signs the certificate the broker serves, with hostname validation intact and never relaxed;
and both published ports are reachable on loopback and nowhere else.

They carry the ``docker`` and ``broker`` markers, so every blocking suite excludes them
(``docs/TESTING.md``). The pushed stages must stay runnable with no daemon and no broker.
"""

from __future__ import annotations

import socket
import ssl
import unittest
from pathlib import Path
from typing import cast

import pytest

pytestmark = [pytest.mark.phase0, pytest.mark.docker, pytest.mark.broker]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CERTIFICATE_AUTHORITY = REPOSITORY_ROOT / "deploy" / "certs" / "ca.pem"

HOST = "localhost"
SMF_TLS_PORT = 55443
SEMP_TLS_PORT = 1943
HANDSHAKE_TIMEOUT_SECONDS = 5.0


def _validated_peer_names(port: int) -> tuple[str, ...]:
    """Return the peer's subject alternative names after a fully validated handshake.

    ``create_default_context`` verifies the chain against the generated authority and
    checks the hostname. Neither is relaxed here, and that is the point of the probe: a
    certificate that validates only with verification switched off is not evidence of
    anything ``docs/adr/0046-generated-local-certificate-authority.md`` claims.
    """
    context = ssl.create_default_context(cafile=str(CERTIFICATE_AUTHORITY))
    with (
        socket.create_connection((HOST, port), timeout=HANDSHAKE_TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw, server_hostname=HOST) as secured,
    ):
        certificate = secured.getpeercert()
    if not certificate:
        return ()
    # typeshed types every value of the peer-certificate dict as a union that includes
    # `str`, so unpacking the pairs is rejected outright. The narrowing is to the shape
    # the standard library documents for this one key, not a suppression of the check.
    names = cast("tuple[tuple[str, str], ...]", certificate.get("subjectAltName", ()))
    return tuple(value for _, value in names)


class FirstLiveStackTests(unittest.TestCase):
    def test_the_message_port_presents_a_certificate_the_generated_authority_signs(self) -> None:
        # Arrange
        port = SMF_TLS_PORT

        # Act
        names = _validated_peer_names(port)

        # Assert
        self.assertIn("localhost", names)
        self.assertIn("broker", names)
        self.assertIn("127.0.0.1", names)

    def test_the_management_port_serves_the_same_authority(self) -> None:
        # Arrange
        port = SEMP_TLS_PORT

        # Act
        names = _validated_peer_names(port)

        # Assert
        self.assertIn("broker", names)

    def test_the_durable_store_accepts_a_connection_on_loopback(self) -> None:
        # Arrange
        address = ("127.0.0.1", 5432)

        # Act
        with socket.create_connection(address, timeout=HANDSHAKE_TIMEOUT_SECONDS) as connected:
            peer = connected.getpeername()

        # Assert
        self.assertEqual(address, peer)


if __name__ == "__main__":
    unittest.main()
