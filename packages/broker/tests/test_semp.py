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


def _document(
    data: object,
    *,
    code: str = "",
    collections: object | None = None,
    description: str = "",
    cursor: str = "",
) -> bytes:
    """Return a SEMP result document carrying data, collections, an error, and a cursor."""
    meta: dict[str, object] = {"responseCode": 200}
    if cursor:
        meta["paging"] = {"cursorQuery": cursor}
    if code:
        meta = {"responseCode": 400, "error": {"code": code, "description": description}}
    document: dict[str, object] = {"data": data, "meta": meta}
    if collections is not None:
        document["collections"] = collections
    return json.dumps(document).encode()


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


class RecordingPacer:
    """Count each monitor-plane request without sleeping."""

    def __init__(self) -> None:
        """Start before any monitor request."""
        self.calls = 0

    def pace(self) -> None:
        """Record one paced monitor request."""
        self.calls += 1


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
        connection = FakeConnection(
            FakeResponse(200, json.dumps({"meta": {"responseCode": 200}}).encode())
        )

        # Act
        rows = SempSession(connection, ENDPOINT).send(PLAIN_REQUEST)

        # Assert
        self.assertEqual((), rows)

    def test_an_empty_no_content_success_is_one_valid_empty_result(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(204, b""))

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

    def test_a_collection_with_one_non_object_row_is_refused_instead_of_truncated(self) -> None:
        # Arrange
        connection = FakeConnection(
            FakeResponse(200, _document([{"queueName": "q1"}, "not-an-object"]))
        )

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)

    def test_a_success_response_without_the_mandatory_meta_object_is_refused(self) -> None:
        # Arrange
        response = FakeResponse(200, json.dumps({"data": {"queueName": "q1"}}).encode())
        connection = FakeConnection(response)

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)

    def test_a_meta_response_code_that_disagrees_with_http_is_refused(self) -> None:
        # Arrange
        payload = json.dumps({"data": {}, "meta": {"responseCode": 503}}).encode()
        response = FakeResponse(200, payload)
        connection = FakeConnection(response)

        # Act
        failure, _ = _failure_of(connection, PLAIN_REQUEST)

        # Assert
        self.assertIs(SempFailure.MALFORMED, failure)


class ConfigSpecTests(unittest.TestCase):
    def test_required_fields_are_verified_against_the_brokers_openapi_spec(self) -> None:
        # Arrange
        spec = {
            "swagger": "2.0",
            "definitions": {
                "MsgVpnQueue": {
                    "properties": {"deadMsgQueue": {"type": "string"}, "maxMsgSize": {}}
                }
            },
        }
        connection = FakeConnection(FakeResponse(200, json.dumps(spec).encode()))
        required = {"MsgVpnQueue": frozenset({"deadMsgQueue", "maxMsgSize"})}

        # Act
        SempSession(connection, ENDPOINT).require_config_fields(required)

        # Assert
        self.assertEqual(
            ("GET", f"{SEMP_CONFIG_PATH}/spec", None),
            connection.calls[0][:3],
        )

    def test_a_required_field_missing_from_the_pinned_spec_is_a_typed_refusal(self) -> None:
        # Arrange
        spec = {
            "swagger": "2.0",
            "definitions": {"MsgVpnQueue": {"properties": {"deadMsgQueue": {}}}},
        }
        connection = FakeConnection(FakeResponse(200, json.dumps(spec).encode()))
        required = {"MsgVpnQueue": frozenset({"deadMsgQueue", "maxMsgSize"})}

        # Act
        try:
            SempSession(connection, ENDPOINT).require_config_fields(required)
        except SempError as error:
            captured = error
        else:
            message = "a missing pinned queue field was accepted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(
            (SempFailure.SPEC, True, True),
            (
                captured.failure,
                "MsgVpnQueue" in str(captured.value),
                "maxMsgSize" in str(captured.value),
            ),
        )

    def test_malformed_spec_collections_and_schemas_are_refused(self) -> None:
        # Arrange
        documents: tuple[object, ...] = (
            {},
            {"definitions": []},
            {"definitions": {"MsgVpnQueue": []}},
            {"definitions": {"MsgVpnQueue": {"properties": []}}},
        )
        required = {"MsgVpnQueue": frozenset({"deadMsgQueue"})}

        # Act
        failures = []
        for document in documents:
            connection = FakeConnection(FakeResponse(200, json.dumps(document).encode()))
            with pytest.raises(SempError) as captured:
                SempSession(connection, ENDPOINT).require_config_fields(required)
            failures.append(captured.value.failure)

        # Assert
        self.assertEqual([SempFailure.SPEC] * len(documents), failures)

    def test_spec_transport_status_and_document_failures_are_typed(self) -> None:
        # Arrange
        connections = (
            FakeConnection(failure=ConnectionRefusedError("no listener")),
            FakeConnection(FakeResponse(503, b"{}")),
            FakeConnection(FakeResponse(200, b"not-json")),
            FakeConnection(FakeResponse(200, b"[]")),
        )
        expected = (
            SempFailure.TRANSPORT,
            SempFailure.STATUS,
            SempFailure.MALFORMED,
            SempFailure.MALFORMED,
        )

        # Act
        failures = []
        for connection in connections:
            with pytest.raises(SempError) as captured:
                SempSession(connection, ENDPOINT).require_config_fields({})
            failures.append(captured.value.failure)

        # Assert
        self.assertEqual(list(expected), failures)

    def test_the_verified_spec_is_cached_for_the_session_epoch(self) -> None:
        # Arrange
        spec: dict[str, object] = {
            "definitions": {"MsgVpnQueue": {"properties": {"deadMsgQueue": {}}}}
        }
        connection = FakeConnection(FakeResponse(200, json.dumps(spec).encode()))
        session = SempSession(connection, ENDPOINT)
        required = {"MsgVpnQueue": frozenset({"deadMsgQueue"})}

        # Act
        session.require_config_fields(required)
        session.require_config_fields(required)

        # Assert
        self.assertEqual(1, len(connection.calls))


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

    def test_a_present_paging_member_with_no_text_cursor_is_refused(self) -> None:
        # Arrange
        payload = json.dumps(
            {
                "data": [{"queueName": "q1"}],
                "meta": {"responseCode": 200, "paging": {"cursorQuery": 7}},
            }
        ).encode()
        connection = FakeConnection(FakeResponse(200, payload))

        # Act
        try:
            SempSession(connection, ENDPOINT).read_all("msgVpns/default/queues")
        except SempError as error:
            captured = error
        else:
            message = "an ill-typed paging cursor was treated as the end"
            raise AssertionError(message)

        # Assert
        self.assertIs(SempFailure.MALFORMED, captured.failure)


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

    def test_queue_monitor_rows_preserve_the_alignment_of_data_and_child_counts(self) -> None:
        # Arrange
        data = [{"queueName": "q1"}, {"queueName": "q2"}]
        collections = [{"msgs": {"count": 50}}, {"msgs": {"count": 75}}]
        connection = FakeConnection(FakeResponse(200, _document(data, collections=collections)))

        # Act
        rows = SempSession(connection, ENDPOINT).read_monitor_rows(
            "msgVpns/default/queues?select=queueName,msgs.count"
        )

        # Assert
        self.assertEqual(
            ((data[0], collections[0]), (data[1], collections[1])),
            tuple((row.data, row.collections) for row in rows),
        )

    def test_monitor_collection_count_reads_the_total_without_walking_child_rows(self) -> None:
        # Arrange
        document = {
            "data": [{"flowId": "first-page-only"}],
            "meta": {"count": 7, "responseCode": 200},
        }
        connection = FakeConnection(FakeResponse(200, json.dumps(document).encode()))
        session = SempSession(connection, ENDPOINT)

        # Act
        count = session.read_monitor_count("msgVpns/default/queues/q/txFlows")

        # Assert
        self.assertEqual(7, count)
        self.assertTrue(connection.calls[0][1].startswith(f"{SEMP_MONITOR_PATH}/"))
        self.assertTrue(connection.calls[0][1].endswith("/txFlows?count=1"))

    def test_monitor_collection_count_refuses_every_non_exact_nonnegative_integer(self) -> None:
        # Arrange
        counts = (None, True, "1", 1.0, -1)

        # Act
        failures = []
        for count in counts:
            document = {"data": [], "meta": {"count": count, "responseCode": 200}}
            connection = FakeConnection(FakeResponse(200, json.dumps(document).encode()))
            with pytest.raises(SempError) as captured:
                SempSession(connection, ENDPOINT).read_monitor_count(
                    "msgVpns/default/queues/q/txFlows"
                )
            failures.append(captured.value.failure)

        # Assert
        self.assertEqual([SempFailure.MALFORMED] * len(counts), failures)

    def test_monitor_collection_count_preserves_an_existing_query_and_is_paced(self) -> None:
        # Arrange
        document = {"data": [], "meta": {"count": 0, "responseCode": 200}}
        connection = FakeConnection(FakeResponse(200, json.dumps(document).encode()))
        pacer = RecordingPacer()
        session = SempSession(connection, ENDPOINT, monitor_pacer=pacer)

        # Act
        count = session.read_monitor_count("msgVpns/default/queues/q/txFlows?where=x")

        # Assert
        self.assertEqual(0, count)
        self.assertEqual(1, pacer.calls)
        self.assertTrue(connection.calls[0][1].endswith("/txFlows?where=x&count=1"))

    def test_queue_monitor_rows_refuse_misaligned_child_counts(self) -> None:
        # Arrange
        connection = FakeConnection(
            FakeResponse(
                200,
                _document(
                    [{"queueName": "q1"}, {"queueName": "q2"}],
                    collections=[{"msgs": {"count": 1}}],
                ),
            )
        )

        # Act
        try:
            SempSession(connection, ENDPOINT).read_monitor_rows("msgVpns/default/queues")
        except SempError as error:
            captured = error
        else:
            message = "misaligned queue and child-collection rows were accepted"
            raise AssertionError(message)

        # Assert
        self.assertIs(SempFailure.MALFORMED, captured.failure)

    def test_queue_monitor_rows_refuse_non_collection_or_non_object_members(self) -> None:
        # Arrange
        documents = (
            {"data": {}, "collections": [], "meta": {"responseCode": 200}},
            {"data": [], "collections": {}, "meta": {"responseCode": 200}},
            {"data": ["bad"], "collections": [{}], "meta": {"responseCode": 200}},
            {"data": [{}], "collections": ["bad"], "meta": {"responseCode": 200}},
        )

        # Act
        failures = []
        for document in documents:
            connection = FakeConnection(FakeResponse(200, json.dumps(document).encode()))
            with pytest.raises(SempError) as captured:
                SempSession(connection, ENDPOINT).read_monitor_rows("msgVpns/default/queues")
            failures.append(captured.value.failure)

        # Assert
        self.assertEqual([SempFailure.MALFORMED] * len(documents), failures)

    def test_aligned_monitor_rows_follow_cursors_and_refuse_an_endless_cursor(self) -> None:
        # Arrange
        pages = PagingConnection(
            [
                _document([{"queueName": "q1"}], collections=[{}], cursor="next"),
                _document([{"queueName": "q2"}], collections=[{}]),
            ]
        )
        endless = PagingConnection(
            [_document([{"queueName": "q"}], collections=[{}], cursor="next")] * (MAX_PAGES + 1)
        )

        # Act
        rows = SempSession(pages, ENDPOINT).read_monitor_rows("msgVpns/default/queues")
        with pytest.raises(SempError) as captured:
            SempSession(endless, ENDPOINT).read_monitor_rows("msgVpns/default/queues")

        # Assert
        self.assertEqual(("q1", "q2"), tuple(row.data["queueName"] for row in rows))
        self.assertEqual(
            (SempFailure.PAGING, MAX_PAGES),
            (captured.value.failure, len(endless.calls)),
        )

    def test_an_existing_monitor_select_is_preserved_when_the_page_bound_is_added(self) -> None:
        # Arrange
        connection = FakeConnection(FakeResponse(200, _document([], collections=[])))
        path = "msgVpns/default/queues?select=queueName,msgs.count"

        # Act
        SempSession(connection, ENDPOINT).read_monitor_rows(path)

        # Assert
        self.assertIn(f"&count={PAGE_SIZE}", connection.calls[0][1])


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

    def test_the_endpoint_representation_redacts_its_password(self) -> None:
        # Arrange
        endpoint = ENDPOINT

        # Act
        represented = repr(endpoint)

        # Assert
        self.assertEqual((False, True), (CREDENTIAL in represented, "<redacted>" in represented))


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
