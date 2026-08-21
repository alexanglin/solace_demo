"""The SEMP transport: one HTTPS connection, bounded, and refusing to leak a credential.

The transport is the only place in this package that performs input or output, and the
only place a client username's password crosses a wire. Two of its rules exist for that
reason and are asserted here: a failure detail names the request through ``describe`` and
never through its body, and the broker's own free-text error description is withheld
whenever the request carried a secret member, because an attribute-level complaint can
quote the value it is complaining about.

The connection is injected, so every case below runs with no broker and no socket.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from enum import Enum

from aerial_rescue_broker.provisioning import Method, Request
from aerial_rescue_broker.semp import (
    REQUEST_TIMEOUT_SECONDS,
    RETRY_COUNT,
    SEMP_CONFIG_PATH,
    SempEndpoint,
    SempError,
    SempFailure,
    SempSession,
    connect,
)

CREDENTIAL = "fixture-not-a-real-credential"
ENDPOINT = SempEndpoint("localhost", 1943, "admin", CREDENTIAL, "deploy/certs/ca.pem")
SECRET_REQUEST = Request(
    Method.PUT, "msgVpns/default/clientUsernames/recorder", {"password": CREDENTIAL}
)
PLAIN_REQUEST = Request(Method.GET, "msgVpns/default/aclProfiles", {})


class FakeResponse:
    """The two members of an HTTP response the session reads."""

    def __init__(self, status: int, payload: bytes) -> None:
        """Hold the status line's code and the body the session will read once."""
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        """Return the response body."""
        return self._payload


class FakeConnection:
    """An HTTPS connection that answers from a script and records what it was asked."""

    def __init__(
        self, response: FakeResponse | None = None, failure: OSError | None = None
    ) -> None:
        """Answer every request with ``response``, or raise ``failure`` instead."""
        self.response = response or FakeResponse(200, b'{"data":{},"meta":{"responseCode":200}}')
        self.failure = failure
        self.calls: list[tuple[str, str, str | None, Mapping[str, str]]] = []

    def request(self, method: str, url: str, body: str | None, headers: Mapping[str, str]) -> None:
        """Record the call, or raise the scripted transport failure."""
        self.calls.append((method, url, body, dict(headers)))
        if self.failure is not None:
            raise self.failure

    def getresponse(self) -> FakeResponse:
        """Return the scripted response."""
        return self.response


def _document(data: object, *, code: str = "", description: str = "") -> bytes:
    """Return a SEMP result document carrying ``data`` and an optional error."""
    meta: dict[str, object] = {"responseCode": 200}
    if code:
        meta = {"responseCode": 400, "error": {"code": code, "description": description}}
    return json.dumps({"data": data, "meta": meta}).encode()


def _failure_of(connection: FakeConnection, request: Request) -> tuple[Enum, object]:
    """Return the failure sending ``request`` raises, failing the test if it succeeds."""
    try:
        SempSession(connection, ENDPOINT).send(request)
    except SempError as error:
        return (error.failure, error.value)
    message = f"accepted: {request!r}"
    raise AssertionError(message)


class SendTests(unittest.TestCase):
    def test_an_object_result_is_returned_as_one_row(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document({"aclProfileName": "recorder"})))

        # Act
        rows = SempSession(connection, ENDPOINT).send(PLAIN_REQUEST)

        # Assert
        self.assertEqual(({"aclProfileName": "recorder"},), rows)

    def test_a_collection_result_is_returned_row_by_row(self) -> None:
        # Arrange
        data = [{"aclProfileName": "recorder"}, {"aclProfileName": "discovery"}]
        connection = FakeConnection(FakeResponse(200, _document(data)))

        # Act
        rows = SempSession(connection, ENDPOINT).send(PLAIN_REQUEST)

        # Assert
        self.assertEqual(tuple(data), rows)

    def test_a_result_with_no_data_member_is_no_rows(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, json.dumps({"meta": {}}).encode()))

        # Act
        rows = SempSession(connection, ENDPOINT).send(PLAIN_REQUEST)

        # Assert
        self.assertEqual((), rows)

    def test_the_request_carries_the_configuration_path_and_a_json_body(self) -> None:
        # Arrange
        connection = FakeConnection()
        request = Request(Method.POST, "msgVpns/default/aclProfiles", {"aclProfileName": "x"})

        # Act
        SempSession(connection, ENDPOINT).send(request)

        # Assert
        self.assertEqual(
            ("POST", f"{SEMP_CONFIG_PATH}/msgVpns/default/aclProfiles", '{"aclProfileName": "x"}'),
            connection.calls[0][:3],
        )

    def test_a_request_with_no_body_sends_none_rather_than_an_empty_object(self) -> None:
        # Arrange
        connection = FakeConnection()

        # Act
        SempSession(connection, ENDPOINT).send(PLAIN_REQUEST)

        # Assert
        self.assertIsNone(connection.calls[0][2])

    def test_a_refused_status_names_the_code_and_the_broker_description(self) -> None:
        # Arrange
        payload = _document(None, code="ALREADY_EXISTS", description="object already exists")
        connection = FakeConnection(FakeResponse(400, payload))

        # Act
        failure, value = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertEqual(
            (SempFailure.STATUS, True, True),
            (failure, "ALREADY_EXISTS" in str(value), "object already exists" in str(value)),
        )

    def test_a_refusal_of_a_request_with_a_secret_withholds_the_broker_description(self) -> None:
        # Arrange
        payload = _document(None, code="INVALID_PARAMETER", description=f"bad value {CREDENTIAL}")
        connection = FakeConnection(FakeResponse(400, payload))

        # Act
        failure, value = _failure_of(connection, SECRET_REQUEST)

        # Assert
        self.assertEqual(
            (SempFailure.STATUS, True, False, False),
            (
                failure,
                "INVALID_PARAMETER" in str(value),
                CREDENTIAL in str(value),
                "bad value" in str(value),
            ),
        )

    def test_a_transport_failure_is_a_typed_outcome_that_keeps_its_cause(self) -> None:
        # Arrange
        cause = ConnectionRefusedError("no listener")
        connection = FakeConnection(failure=cause)

        # Act
        try:
            SempSession(connection, ENDPOINT).send(SECRET_REQUEST)
        except SempError as error:
            captured = error
        else:
            message = "the transport failure was swallowed"
            raise AssertionError(message)

        # Assert
        self.assertEqual(
            (SempFailure.TRANSPORT, cause, False),
            (captured.failure, captured.__cause__, CREDENTIAL in str(captured)),
        )

    def test_a_refusal_carrying_no_error_member_still_names_the_status(self) -> None:
        # Arrange
        payload = json.dumps({"meta": {"responseCode": 503}}).encode()
        connection = FakeConnection(FakeResponse(503, payload))

        # Act
        failure, value = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertEqual(
            (SempFailure.STATUS, True, False),
            (failure, "status=503" in str(value), "code=" in str(value)),
        )

    def test_a_response_that_is_json_but_not_an_object_is_refused(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, b'["aclProfileName"]'))

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)

    def test_a_response_that_is_not_a_semp_result_is_refused(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, b"<html>gateway timeout</html>"))

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)

    def test_a_result_whose_data_is_neither_object_nor_collection_is_refused(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document("not an object")))

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)


class HeaderTests(unittest.TestCase):
    def test_the_authorization_header_is_basic_and_never_appears_in_a_description(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(400, _document(None, code="DENIED")))

        # Act
        failure, value = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertEqual(
            (SempFailure.STATUS, "Basic ", "application/json", False),
            (
                failure,
                connection.calls[0][3]["Authorization"][:6],
                connection.calls[0][3]["Content-Type"],
                CREDENTIAL in str(value),
            ),
        )


class ConnectTests(unittest.TestCase):
    def test_a_connection_is_opened_to_the_endpoint_under_the_bounded_timeout(self) -> None:
        # Arrange
        endpoint = ENDPOINT

        # Act
        connection = connect(endpoint)

        # Assert
        self.assertEqual(
            ("localhost", 1943, REQUEST_TIMEOUT_SECONDS),
            (connection.host, connection.port, connection.timeout),
        )

    def test_the_transport_does_not_retry(self) -> None:
        # Arrange
        connection = FakeConnection(failure=ConnectionResetError("reset"))

        # Act
        _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertEqual(1 + RETRY_COUNT, len(connection.calls))


class SempErrorTests(unittest.TestCase):
    def test_the_message_names_the_failure_and_the_value(self) -> None:
        # Arrange
        error = SempError(SempFailure.MALFORMED, "GET msgVpns/default/aclProfiles")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "the broker's response is not a SEMP result object: 'GET msgVpns/default/aclProfiles'",
            message,
        )


if __name__ == "__main__":
    unittest.main()
