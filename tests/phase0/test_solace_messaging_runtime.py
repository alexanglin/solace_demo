"""Whether the Solace PubSub+ client functions on the application interpreter.

The wheels are tagged ``py36-none-<platform>``, so uv installs them on any Python 3
without complaint. Installing is not evidence: the package loads a bundled native library
through ``ctypes`` and marshals structures and callbacks across that boundary, which is
where an interpreter change actually breaks. These probes exercise that boundary without
a broker, so the answer does not depend on credentials.

``docs/adr/0004-split-python-runtimes.md`` rests on this working. A failure here is a kill
criterion for the split-runtime decision, not a defect to work around. The client's two
static-analysis defects, and why importing it needs a declared relaxation at all, are in
``docs/adr/0028-untyped-solace-client-boundary.md``.
"""

from __future__ import annotations

import ctypes
import importlib.metadata
import sys
import unittest
from pathlib import Path

import pytest
import solace
from solace.messaging.messaging_service import MessagingService

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]

PINNED_VERSION = "1.11.0"
UNROUTED_HOST = "tcp://127.0.0.1:55555"


def _built_service() -> MessagingService:
    """Return a service built from properties, without connecting to anything."""
    return (
        MessagingService.builder()
        .from_properties(
            {
                "solace.messaging.transport.host": UNROUTED_HOST,
                "solace.messaging.service.vpn-name": "default",
                "solace.messaging.authentication.scheme.basic.username": "probe",
                "solace.messaging.authentication.scheme.basic.password": "placeholder",
            }
        )
        .build()
    )


class InstalledDistributionTests(unittest.TestCase):
    def test_the_pinned_client_is_installed_on_the_application_interpreter(self) -> None:
        # Arrange
        expected = (PINNED_VERSION, True)

        # Act
        observed = (
            importlib.metadata.version("solace-pubsubplus"),
            sys.version_info[:2] >= (3, 14),
        )

        # Assert
        self.assertEqual(expected, observed)


class NativeLibraryTests(unittest.TestCase):
    def test_the_bundled_native_library_loads_and_initializes(self) -> None:
        # Arrange
        expected_type = ctypes.CDLL

        # Act
        core = solace.CORE_LIB

        # Assert
        self.assertIsInstance(core, expected_type)

    def test_the_loaded_library_is_the_one_bundled_in_the_installed_package(self) -> None:
        # Arrange
        package_root = Path(solace.__file__).resolve().parent

        # Act
        loaded = Path(solace.CORE_LIB._name).resolve()

        # Assert
        self.assertTrue(loaded.is_relative_to(package_root), loaded)


class SessionMarshallingTests(unittest.TestCase):
    def test_a_service_builds_from_properties_without_connecting(self) -> None:
        # Arrange
        expected_connected = False

        # Act
        service = _built_service()

        # Assert
        self.assertEqual(expected_connected, service.is_connected)

    def test_the_api_version_reads_back_across_the_native_boundary(self) -> None:
        # Arrange
        service = _built_service()

        # Act
        version = service.info().get_api_version()

        # Assert
        self.assertRegex(version, r"\d+\.\d+\.\d+")

    def test_the_service_reports_a_non_empty_application_identifier(self) -> None:
        # Arrange
        service = _built_service()

        # Act
        identifier = service.get_application_id()

        # Assert
        self.assertNotEqual("", identifier)


class MessageMarshallingTests(unittest.TestCase):
    def test_a_payload_survives_the_native_round_trip(self) -> None:
        # Arrange
        service = _built_service()
        builder = service.message_builder()

        # Act
        message = builder.build("rescue-artifact")

        # Assert
        self.assertEqual("rescue-artifact", message.get_payload_as_string())


if __name__ == "__main__":
    unittest.main()
