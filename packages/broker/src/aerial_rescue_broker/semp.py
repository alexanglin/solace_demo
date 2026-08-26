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
from typing import Final, Protocol
from urllib.parse import quote

from aerial_rescue_broker.provisioning import SECRET_MEMBERS, Method, Request, describe

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


class SempFailure(Enum):
    """Why a SEMP call did not produce a result."""

    TRANSPORT = "the SEMP request could not be completed"
    STATUS = "the broker refused the SEMP request"
    MALFORMED = "the broker's response is not a SEMP result object"
    PAGING = "the collection did not end within the page bound"


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
    paging = meta.get("paging") if isinstance(meta, Mapping) else None
    cursor = paging.get("cursorQuery") if isinstance(paging, Mapping) else None
    return cursor if isinstance(cursor, str) else None


def _rows(request: Request, data: object) -> tuple[Mapping[str, object], ...]:
    """Return the ``data`` member as a tuple of objects, refusing any other shape."""
    if data is None:
        return ()
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, list):
        return tuple(row for row in data if isinstance(row, Mapping))
    raise SempError(SempFailure.MALFORMED, describe(request))


class SempSession:
    """A ``SempTransport`` over one injected HTTPS connection."""

    def __init__(self, connection: HttpConnection, endpoint: SempEndpoint) -> None:
        """Bind the session to a connection and the credential it authenticates with."""
        self._connection = connection
        self._headers = _headers(endpoint)

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

    def _read_paged(self, path: str, root: str) -> tuple[Mapping[str, object], ...]:
        """Walk one collection's cursor to its end, or refuse at the page bound."""
        rows: list[Mapping[str, object]] = []
        query = f"?count={PAGE_SIZE}"
        for _ in range(MAX_PAGES):
            request = Request(Method.GET, path + query, {})
            document = self._perform(request, root)
            rows.extend(_rows(request, document.get("data")))
            cursor = _cursor(document)
            if cursor is None:
                return tuple(rows)
            query = f"?count={PAGE_SIZE}&cursor={quote(cursor, safe='')}"
        raise SempError(SempFailure.PAGING, path)

    def _perform(self, request: Request, root: str = SEMP_CONFIG_PATH) -> Mapping[str, object]:
        """Send one request under ``root`` and return the whole SEMP result document."""
        body = json.dumps(dict(request.body)) if request.body else None
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
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SempError(SempFailure.MALFORMED, describe(request)) from error
        if not isinstance(document, Mapping):
            raise SempError(SempFailure.MALFORMED, describe(request))
        if status not in _SUCCESS_STATUS:
            raise SempError(SempFailure.STATUS, _detail(request, status, document))
        return document
