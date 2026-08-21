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

from aerial_rescue_broker.provisioning import SECRET_MEMBERS, Request, describe

SEMP_CONFIG_PATH: Final = "/SEMP/v2/config"
"""The configuration API's root, below which every request path is relative."""

REQUEST_TIMEOUT_SECONDS: Final = 10.0
"""Bound on one SEMP call; the value's home is docs/operating-parameters.md."""

RETRY_COUNT: Final = 0
"""Retries per call. Re-running the convergent apply is the retry; see the module docstring."""

_SUCCESS_STATUS: Final = range(200, 300)


class SempFailure(Enum):
    """Why a SEMP call did not produce a result."""

    TRANSPORT = "the SEMP request could not be completed"
    STATUS = "the broker refused the SEMP request"
    MALFORMED = "the broker's response is not a SEMP result object"


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


def connect(endpoint: SempEndpoint) -> http.client.HTTPSConnection:
    """Return a connection to ``endpoint``, validating its chain against the authority.

    Args:
        endpoint: The broker's SEMP host and port and the authority that signs it.

    Returns:
        An unopened connection; ``http.client`` connects on the first request. Verification
        and hostname checking are the defaults of ``ssl.create_default_context`` and are
        never relaxed.
    """
    context = ssl.create_default_context(cafile=endpoint.certificate_authority)
    return http.client.HTTPSConnection(
        endpoint.host, endpoint.port, context=context, timeout=REQUEST_TIMEOUT_SECONDS
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
            none when the result carries no ``data``.

        Raises:
            SempError: With ``TRANSPORT`` when the call could not be made, preserving the
                operating-system error as ``__cause__``; with ``STATUS`` when the broker
                refused it; and with ``MALFORMED`` when the response is not a SEMP result.
        """
        body = json.dumps(dict(request.body)) if request.body else None
        try:
            self._connection.request(
                request.method.value, f"{SEMP_CONFIG_PATH}/{request.path}", body, self._headers
            )
            response = self._connection.getresponse()
            payload = response.read()
        except OSError as error:
            raise SempError(SempFailure.TRANSPORT, describe(request)) from error
        return self._result(request, response.status, payload)

    def _result(
        self, request: Request, status: int, payload: bytes
    ) -> tuple[Mapping[str, object], ...]:
        """Parse one response, refusing a non-SEMP body and a refused status."""
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SempError(SempFailure.MALFORMED, describe(request)) from error
        if not isinstance(document, Mapping):
            raise SempError(SempFailure.MALFORMED, describe(request))
        if status not in _SUCCESS_STATUS:
            raise SempError(SempFailure.STATUS, _detail(request, status, document))
        return _rows(request, document.get("data"))
