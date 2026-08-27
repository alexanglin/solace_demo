"""The SEMP v2 config transport: one bounded HTTPS connection that never leaks a credential.

This is the only module in the package that performs input or output, and the only place a
client username's password crosses a wire. Three rules follow from that and none of them is
incidental.

**A failure names the request, never its body.** Every message is built from
:func:`~aerial_rescue_broker.provisioning.describe`, which redacts secret members. The
broker's own free-text error description is included only when the request carried no
secret member, because an attribute-level complaint can quote the value it is complaining
about, and ``AGENTS.md`` does not make an exception for a value the broker chose to echo.

**The transport does not retry.** ``RETRY_COUNT`` is zero, and that is a decision rather
than an omission: a ``POST`` of a topic exception is not idempotent, so a blind retry after
an ambiguous failure would turn one refusal into a second, different one. Re-running the
whole apply is the retry, and it is safe because
:func:`~aerial_rescue_broker.provisioning.apply` converges.

**Everything is injected.** The connection is a protocol, so every behaviour here is tested
with no broker and no socket, and :func:`connect` is the one function that names a real
class. It validates the chain against the per-checkout authority from
``docs/adr/0046-generated-local-certificate-authority.md`` with hostname checking left on,
the same way ``tests/phase0/test_first_live_stack.py`` does.
"""

from __future__ import annotations

import http.client
import json
import ssl
from base64 import b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol, override
from urllib.parse import quote

from aerial_rescue_broker.provisioning import (
    REDACTED,
    SECRET_MEMBERS,
    Method,
    MonitorRow,
    Request,
    describe,
)

SEMP_CONFIG_PATH: Final = "/SEMP/v2/config"
"""The configuration API's root, below which every request path is relative."""

SEMP_MONITOR_PATH: Final = "/SEMP/v2/monitor"
"""The monitoring API's root. Read-only, and reachable only through :meth:`read_monitor`."""

REQUEST_TIMEOUT_SECONDS: Final = 10.0
"""Bound on one SEMP call; the value's home is docs/operating-parameters.md."""

RETRY_COUNT: Final = 0
"""Retries per call. Re-running the convergent apply is the retry; see the module docstring."""

PAGE_SIZE: Final = 100
"""Rows asked for per collection read. The broker pages at ten unless asked for more."""

MAX_PAGES: Final = 20
"""Bound on one collection read. An unbounded cursor loop is a hang, not a read."""

_SUCCESS_STATUS: Final = range(200, 300)
_NO_CONTENT_STATUS: Final = 204


class SempFailure(Enum):
    """Why a SEMP call did not produce a result."""

    TRANSPORT = "the SEMP request could not be completed"
    STATUS = "the broker refused the SEMP request"
    MALFORMED = "the broker's response is not a SEMP result object"
    PAGING = "the collection did not end within the page bound"
    SPEC = "the pinned broker specification lacks a required configuration field"


class SempError(RuntimeError):
    """A SEMP call that failed, carrying the failure as structured data."""

    failure: SempFailure
    value: object

    def __init__(self, failure: SempFailure, value: object) -> None:
        """Record the structured failure alongside the redacted request that caused it."""
        super().__init__(f"{failure.value}: {value!r}")
        self.failure = failure
        self.value = value


@dataclass(frozen=True)
class SempEndpoint:
    """Where the configuration API is, who is calling it, and what signs its certificate."""

    host: str
    port: int
    username: str
    password: str
    certificate_authority: str

    @override
    def __repr__(self) -> str:
        """Render the endpoint without exposing its management credential."""
        return (
            "SempEndpoint("
            f"host={self.host!r}, port={self.port!r}, username={self.username!r}, "
            f"password={REDACTED!r}, certificate_authority={self.certificate_authority!r})"
        )


class HttpResponse(Protocol):
    """The two members of an HTTP response this module reads."""

    status: int

    def read(self) -> bytes:
        """Return the response body."""
        ...


class HttpConnection(Protocol):
    """The HTTPS connection surface this module uses, injected so no test opens a socket."""

    def request(self, method: str, url: str, body: str | None, headers: Mapping[str, str]) -> None:
        """Send one request."""
        ...

    def getresponse(self) -> HttpResponse:
        """Return the response to the request just sent."""
        ...


class MonitorPacer(Protocol):
    """The one operation that spaces routine monitor page requests."""

    def pace(self) -> None:
        """Wait only as needed before one monitor-plane request."""
        ...


def verification_context(authority: str) -> ssl.SSLContext:
    """Return a context that verifies against ``authority`` with hostname checking left on.

    Args:
        authority: Path to the per-checkout authority certificate.

    Returns:
        A context built by ``ssl.create_default_context``, whose verification and hostname
        checking defaults are never relaxed here.

    Raises:
        OSError: When ``authority`` cannot be read, which is the evidence that the named
            file is loaded rather than the system trust store being silently accepted.
    """
    return ssl.create_default_context(cafile=authority)


def connect(
    endpoint: SempEndpoint, *, context: ssl.SSLContext | None = None
) -> http.client.HTTPSConnection:
    """Return a connection to ``endpoint``, validating its chain against the authority.

    Args:
        endpoint: The broker's SEMP host and port and the authority that signs it.
        context: The TLS context, injected only so tests need no generated material on
            disk. Omitted, it is built from the endpoint's authority.

    Returns:
        An unopened connection; ``http.client`` connects on the first request.
    """
    resolved = verification_context(endpoint.certificate_authority) if context is None else context
    return http.client.HTTPSConnection(
        endpoint.host, endpoint.port, context=resolved, timeout=REQUEST_TIMEOUT_SECONDS
    )


def _headers(endpoint: SempEndpoint) -> dict[str, str]:
    """Return the request headers, including the basic credential this module never logs."""
    raw = f"{endpoint.username}:{endpoint.password}".encode()
    return {
        "Authorization": "Basic " + b64encode(raw).decode(),
        "Content-Type": "application/json",
    }


def _detail(request: Request, status: int, document: Mapping[str, object]) -> str:
    """Return a refusal detail, withholding the broker's free text when a secret was sent."""
    meta = document.get("meta")
    error = meta.get("error") if isinstance(meta, Mapping) else None
    parts = [describe(request), f"status={status}"]
    if isinstance(error, Mapping):
        parts.append(f"code={error.get('code')}")
        if not SECRET_MEMBERS & set(request.body):
            parts.append(f"description={error.get('description')!r}")
    return " ".join(parts)


def _cursor(document: Mapping[str, object]) -> str | None:
    """Return the paging cursor a partial collection carries, or ``None`` when it is whole."""
    meta = document.get("meta")
    if not isinstance(meta, Mapping) or "paging" not in meta:
        return None
    paging = meta["paging"]
    cursor = paging.get("cursorQuery") if isinstance(paging, Mapping) else None
    if not isinstance(cursor, str) or not cursor:
        raise SempError(SempFailure.MALFORMED, "paging cursor")
    return cursor


def _rows(request: Request, data: object) -> tuple[Mapping[str, object], ...]:
    """Return the ``data`` member as a tuple of objects, refusing any other shape."""
    if data is None:
        return ()
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, list) and all(isinstance(row, Mapping) for row in data):
        return tuple(data)
    raise SempError(SempFailure.MALFORMED, describe(request))


def _monitor_rows(request: Request, document: Mapping[str, object]) -> tuple[MonitorRow, ...]:
    """Return collection rows aligned with their child-collection count objects."""
    data = document.get("data")
    collections = document.get("collections")
    if not isinstance(data, list) or not isinstance(collections, list):
        raise SempError(SempFailure.MALFORMED, describe(request))
    if len(data) != len(collections):
        raise SempError(SempFailure.MALFORMED, describe(request))
    if not all(isinstance(row, Mapping) for row in data):
        raise SempError(SempFailure.MALFORMED, describe(request))
    if not all(isinstance(row, Mapping) for row in collections):
        raise SempError(SempFailure.MALFORMED, describe(request))
    return tuple(
        MonitorRow(data=data_row, collections=collection_row)
        for data_row, collection_row in zip(data, collections, strict=True)
    )


def _monitor_count(request: Request, document: Mapping[str, object]) -> int:
    """Return one exact non-negative monitor collection count."""
    meta = document.get("meta")
    count = meta.get("count") if isinstance(meta, Mapping) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise SempError(SempFailure.MALFORMED, describe(request))
    return count


class SempSession:
    """A ``SempTransport`` over one injected HTTPS connection."""

    def __init__(
        self,
        connection: HttpConnection,
        endpoint: SempEndpoint,
        *,
        monitor_pacer: MonitorPacer | None = None,
    ) -> None:
        """Bind the session to a connection, credential, and optional monitor pacer."""
        self._connection = connection
        self._headers = _headers(endpoint)
        self._config_spec: Mapping[str, object] | None = None
        self._monitor_pacer = monitor_pacer

    def require_config_fields(self, required: Mapping[str, frozenset[str]]) -> None:
        """Refuse unless the broker's own OpenAPI 2 spec declares every required field."""
        document = self._read_config_spec()
        definitions = document.get("definitions")
        if not isinstance(definitions, Mapping):
            raise SempError(SempFailure.SPEC, "definitions")
        for schema_name, required_fields in required.items():
            schema = definitions.get(schema_name)
            properties = schema.get("properties") if isinstance(schema, Mapping) else None
            if not isinstance(properties, Mapping):
                raise SempError(SempFailure.SPEC, schema_name)
            available = frozenset(name for name in properties if isinstance(name, str))
            missing = required_fields - available
            if missing:
                raise SempError(
                    SempFailure.SPEC,
                    {"schema": schema_name, "missing": tuple(sorted(missing))},
                )

    def _read_config_spec(self) -> Mapping[str, object]:
        """Read and cache the broker-owned OpenAPI document, which has no SEMP envelope."""
        if self._config_spec is not None:
            return self._config_spec
        request = Request(Method.GET, "spec", {})
        try:
            self._connection.request(
                request.method.value,
                f"{SEMP_CONFIG_PATH}/{request.path}",
                None,
                self._headers,
            )
            response = self._connection.getresponse()
            payload = response.read()
        except OSError as error:
            raise SempError(SempFailure.TRANSPORT, describe(request)) from error
        if response.status not in _SUCCESS_STATUS:
            raise SempError(
                SempFailure.STATUS,
                f"{describe(request)} status={response.status}",
            )
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SempError(SempFailure.MALFORMED, describe(request)) from error
        if not isinstance(document, Mapping):
            raise SempError(SempFailure.MALFORMED, describe(request))
        self._config_spec = document
        return document

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Perform ``request`` and return its ``data`` member as a tuple of objects.

        Args:
            request: The method, configuration-relative path, and body to send.

        Returns:
            The result rows: one for an object result, one per row for a collection, and
            none when the result carries no ``data``. A collection read this way is only
            the broker's first page; use :meth:`read_all` for a whole one.

        Raises:
            SempError: With ``TRANSPORT`` when the call could not be made, preserving the
                operating-system error as ``__cause__``; with ``STATUS`` when the broker
                refused it; and with ``MALFORMED`` when the response is not a SEMP result.
        """
        return _rows(request, self._perform(request).get("data"))

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the collection at ``path``, following the broker's cursor.

        Args:
            path: The collection's configuration-relative path.

        Returns:
            Every row, across as many pages as the broker splits the collection into.

        Raises:
            SempError: With ``PAGING`` when the cursor has not run out within
                ``MAX_PAGES``, and with the same failures :meth:`send` raises.
        """
        return self._read_paged(path, SEMP_CONFIG_PATH)

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the monitoring collection at ``path``, following the cursor.

        The monitoring API answers questions the configuration API cannot -- how many
        messages a queue is holding right now, rather than how it was configured. It is a
        separate method rather than a flag on :meth:`read_all` because it is the only way
        to reach that root: :meth:`send` performs every write and is bound to the
        configuration root, so no request built here can mutate through the monitor plane.

        Args:
            path: The collection's monitor-relative path.

        Returns:
            Every row, across as many pages as the broker splits the collection into. A
            queue holding more messages than one page is the case this exists for.

        Raises:
            SempError: With ``PAGING`` when the cursor has not run out within
                ``MAX_PAGES``, and with the same failures :meth:`send` raises.
        """
        return self._read_paged(path, SEMP_MONITOR_PATH)

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return monitor rows with each row's child-collection counts kept aligned."""
        rows: list[MonitorRow] = []
        query = self._page_query(path)
        for _ in range(MAX_PAGES):
            request = Request(Method.GET, path + query, {})
            document = self._perform(request, SEMP_MONITOR_PATH)
            rows.extend(_monitor_rows(request, document))
            cursor = _cursor(document)
            if cursor is None:
                return tuple(rows)
            query = self._page_query(path, cursor)
        raise SempError(SempFailure.PAGING, path)

    def read_monitor_count(self, path: str) -> int:
        """Return a monitor collection's total from one response without enumerating it."""
        separator = "&" if "?" in path else "?"
        request = Request(Method.GET, f"{path}{separator}count=1", {})
        return _monitor_count(request, self._perform(request, SEMP_MONITOR_PATH))

    def _read_paged(self, path: str, root: str) -> tuple[Mapping[str, object], ...]:
        """Walk one collection's cursor to its end, or refuse at the page bound."""
        rows: list[Mapping[str, object]] = []
        query = self._page_query(path)
        for _ in range(MAX_PAGES):
            request = Request(Method.GET, path + query, {})
            document = self._perform(request, root)
            rows.extend(_rows(request, document.get("data")))
            cursor = _cursor(document)
            if cursor is None:
                return tuple(rows)
            query = self._page_query(path, cursor)
        raise SempError(SempFailure.PAGING, path)

    @staticmethod
    def _page_query(path: str, cursor: str | None = None) -> str:
        """Return paging query members without replacing a caller's narrow selection."""
        separator = "&" if "?" in path else "?"
        cursor_member = f"&cursor={quote(cursor, safe='')}" if cursor is not None else ""
        return f"{separator}count={PAGE_SIZE}{cursor_member}"

    def _perform(self, request: Request, root: str = SEMP_CONFIG_PATH) -> Mapping[str, object]:
        """Send one request under ``root`` and return the whole SEMP result document."""
        body = json.dumps(dict(request.body)) if request.body else None
        if root == SEMP_MONITOR_PATH and self._monitor_pacer is not None:
            self._monitor_pacer.pace()
        try:
            self._connection.request(
                request.method.value, f"{root}/{request.path}", body, self._headers
            )
            response = self._connection.getresponse()
            payload = response.read()
        except OSError as error:
            raise SempError(SempFailure.TRANSPORT, describe(request)) from error
        return self._document(request, response.status, payload)

    def _document(self, request: Request, status: int, payload: bytes) -> Mapping[str, object]:
        """Parse one response, refusing a non-SEMP body and a refused status."""
        if status == _NO_CONTENT_STATUS and not payload:
            return {"meta": {"responseCode": status}}
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SempError(SempFailure.MALFORMED, describe(request)) from error
        if not isinstance(document, Mapping):
            raise SempError(SempFailure.MALFORMED, describe(request))
        meta = document.get("meta")
        response_code = meta.get("responseCode") if isinstance(meta, Mapping) else None
        if isinstance(response_code, bool) or not isinstance(response_code, int):
            raise SempError(SempFailure.MALFORMED, describe(request))
        if response_code != status:
            raise SempError(SempFailure.MALFORMED, describe(request))
        if status not in _SUCCESS_STATUS:
            raise SempError(SempFailure.STATUS, _detail(request, status, document))
        return document
