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
import ssl
import unittest
from collections.abc import Mapping
from enum import Enum
from typing import override

import pytest
from aerial_rescue_broker.provisioning import Method, Request
from aerial_rescue_broker.semp import (
    MAX_PAGES,
    PAGE_SIZE,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_COUNT,
    SEMP_CONFIG_PATH,
    SEMP_MONITOR_PATH,
    SempEndpoint,
    SempError,
    SempFailure,
    SempSession,
    connect,
    verification_context,
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


def _document(data: object, *, code: str = "", description: str = "", cursor: str = "") -> bytes:
    """Return a SEMP result document carrying ``data``, an optional error, and a cursor."""
    meta: dict[str, object] = {"responseCode": 200}
    if cursor:
        meta["paging"] = {"cursorQuery": cursor}
    if code:
        meta = {"responseCode": 400, "error": {"code": code, "description": description}}
    return json.dumps({"data": data, "meta": meta}).encode()


class PagingConnection(FakeConnection):
    """A connection that answers each successive request from a scripted list of pages."""

    def __init__(self, pages: list[bytes]) -> None:
        """Answer the first request with the first page, and so on."""
        super().__init__()
        self.pages = pages

    @override
    def getresponse(self) -> FakeResponse:
        """Return the page matching how many requests have been made so far."""
        return FakeResponse(200, self.pages[len(self.calls) - 1])


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


class ReadAllTests(unittest.TestCase):
    def test_a_single_page_collection_is_read_in_one_call(self) -> None:
        # Arrange
        rows = [{"subscribeTopicException": "a"}, {"subscribeTopicException": "b"}]
        connection = FakeConnection(FakeResponse(200, _document(rows)))

        # Act
        read = SempSession(connection, ENDPOINT).read_all("msgVpns/default/aclProfiles")

        # Assert
        self.assertEqual((tuple(rows), 1), (read, len(connection.calls)))

    def test_the_first_read_asks_for_more_than_the_brokers_default_page(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document([])))

        # Act
        SempSession(connection, ENDPOINT).read_all("msgVpns/default/aclProfiles")

        # Assert
        self.assertIn(f"?count={PAGE_SIZE}", connection.calls[0][1])

    def test_a_paged_collection_is_followed_to_its_last_row(self) -> None:
        # Arrange
        pages = PagingConnection(
            [
                _document([{"subscribeTopicException": "a"}], cursor="opaque cursor/1"),
                _document([{"subscribeTopicException": "b"}]),
            ]
        )

        # Act
        read = SempSession(pages, ENDPOINT).read_all("msgVpns/default/aclProfiles")

        # Assert
        self.assertEqual(
            (({"subscribeTopicException": "a"}, {"subscribeTopicException": "b"}), 2, True),
            (read, len(pages.calls), "cursor=opaque%20cursor%2F1" in pages.calls[1][1]),
        )

    def test_a_cursor_that_never_runs_out_is_refused_rather_than_looped_on(self) -> None:
        # Arrange
        endless = PagingConnection(
            [_document([{"subscribeTopicException": "a"}], cursor="never ends")] * (MAX_PAGES + 2)
        )

        # Act
        try:
            SempSession(endless, ENDPOINT).read_all("msgVpns/default/aclProfiles")
        except SempError as error:
            captured = error
        else:
            message = "an endless cursor was followed to the end"
            raise AssertionError(message)

        # Assert
        self.assertEqual((SempFailure.PAGING, MAX_PAGES), (captured.failure, len(endless.calls)))


class ReadMonitorTests(unittest.TestCase):
    """The monitor plane: the same bounded walk, a different root, and no way to write."""

    def test_a_monitor_collection_is_read_from_the_monitor_root(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document([])))

        # Act
        SempSession(connection, ENDPOINT).read_monitor("msgVpns/default/queues/q/msgs")

        # Assert
        self.assertTrue(connection.calls[0][1].startswith(f"{SEMP_MONITOR_PATH}/"))

    def test_a_monitor_collection_longer_than_one_page_is_followed_to_its_last_row(self) -> None:
        """The depth a backlog measurement reads is larger than one page, so this is the defect."""
        # Arrange
        pages = PagingConnection(
            [
                _document([{"msgId": index} for index in range(PAGE_SIZE)], cursor="page/2"),
                _document([{"msgId": index} for index in range(PAGE_SIZE)], cursor="page/3"),
                _document([{"msgId": index} for index in range(7)]),
            ]
        )

        # Act
        read = SempSession(pages, ENDPOINT).read_monitor("msgVpns/default/queues/q/msgs")

        # Assert
        self.assertEqual((PAGE_SIZE * 2 + 7, 3), (len(read), len(pages.calls)))

    def test_a_monitor_cursor_that_never_runs_out_is_refused_rather_than_looped_on(self) -> None:
        # Arrange
        endless = PagingConnection(
            [_document([{"msgId": 1}], cursor="never ends")] * (MAX_PAGES + 2)
        )

        # Act
        try:
            SempSession(endless, ENDPOINT).read_monitor("msgVpns/default/queues/q/msgs")
        except SempError as error:
            captured = error
        else:
            message = "an endless monitor cursor was followed to the end"
            raise AssertionError(message)

        # Assert
        self.assertEqual((SempFailure.PAGING, MAX_PAGES), (captured.failure, len(endless.calls)))

    def test_the_configuration_plane_stays_where_it_was(self) -> None:
        """The two roots are separate, so a monitor read can never reach a writable path."""
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document([])))

        # Act
        SempSession(connection, ENDPOINT).read_all("msgVpns/default/aclProfiles")

        # Assert
        self.assertTrue(connection.calls[0][1].startswith(f"{SEMP_CONFIG_PATH}/"))


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
    def test_a_connection_carries_the_endpoint_and_the_bounded_timeout(self) -> None:
        # Arrange
        context = ssl.create_default_context()

        # Act
        connection = connect(ENDPOINT, context=context)

        # Assert
        self.assertEqual(
            ("localhost", 1943, REQUEST_TIMEOUT_SECONDS),
            (connection.host, connection.port, connection.timeout),
        )

    def test_an_authority_that_cannot_be_read_is_not_silently_replaced_by_system_trust(
        self,
    ) -> None:
        # Arrange
        absent = "deploy/certs/never-generated-authority.pem"

        # Act
        with pytest.raises(FileNotFoundError) as captured:
            verification_context(absent)

        # Assert
        self.assertEqual(2, captured.value.errno)

    def test_connect_builds_its_context_from_the_endpoints_authority_when_none_is_given(
        self,
    ) -> None:
        # Arrange
        endpoint = SempEndpoint(
            "localhost", 1943, "admin", CREDENTIAL, "deploy/certs/never-generated.pem"
        )

        # Act
        with pytest.raises(FileNotFoundError) as captured:
            connect(endpoint)

        # Assert
        self.assertEqual(2, captured.value.errno)

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
