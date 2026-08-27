"""Local dashboard HTTP Host, Origin, and bearer boundary tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from aerial_rescue_dashboard_api.boundary.security import (
    BoundaryError,
    BoundaryRefusal,
    LocalOperatorBoundary,
)


def _headers(*values: tuple[str, str]) -> tuple[tuple[bytes, bytes], ...]:
    return tuple((name.encode("ascii"), value.encode("ascii")) for name, value in values)


@pytest.mark.parametrize(
    ("headers", "refusal"),
    [
        ((), BoundaryRefusal.HOST_MISSING),
        (
            _headers(("host", "localhost:8080"), ("host", "localhost:8080")),
            BoundaryRefusal.HOST_MULTIPLE,
        ),
        (_headers(("host", "localhost")), BoundaryRefusal.HOST_MALFORMED),
        (_headers(("host", "localhost:8080.evil.invalid")), BoundaryRefusal.HOST_MALFORMED),
        (_headers(("host", "evil.invalid:8080")), BoundaryRefusal.HOST_NOT_ALLOWED),
    ],
)
def test_boundary_refuses_invalid_host_before_any_other_control(
    headers: Sequence[tuple[bytes, bytes]], refusal: BoundaryRefusal
) -> None:
    # Arrange
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        bearer="secret-current-runtime",
        operator_id="local-operator",
    )

    # Act
    with pytest.raises(BoundaryError) as captured:
        boundary.authorize(headers, mutation=True)

    # Assert
    assert captured.value.refusal is refusal
    assert "secret-current-runtime" not in str(captured.value)


@pytest.mark.parametrize(
    ("origins", "refusal"),
    [
        ((), BoundaryRefusal.ORIGIN_MISSING),
        (("null",), BoundaryRefusal.ORIGIN_MALFORMED),
        (("http://localhost:8080/path",), BoundaryRefusal.ORIGIN_MALFORMED),
        (("https://localhost:8080",), BoundaryRefusal.ORIGIN_NOT_ALLOWED),
        (("http://localhost.evil.invalid:8080",), BoundaryRefusal.ORIGIN_NOT_ALLOWED),
        (
            ("http://localhost:8080", "http://localhost:8080"),
            BoundaryRefusal.ORIGIN_MULTIPLE,
        ),
    ],
)
def test_mutation_refuses_invalid_origin_before_bearer(
    origins: tuple[str, ...], refusal: BoundaryRefusal
) -> None:
    # Arrange
    origin_headers = tuple(("origin", origin) for origin in origins)
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        bearer="secret-current-runtime",
        operator_id="local-operator",
    )
    headers = _headers(
        ("host", "localhost:8080"),
        *origin_headers,
        ("authorization", "Bearer wrong"),
    )

    # Act
    with pytest.raises(BoundaryError) as captured:
        boundary.authorize(headers, mutation=True)

    # Assert
    assert captured.value.refusal is refusal


@pytest.mark.parametrize(
    ("authorizations", "refusal"),
    [
        ((), BoundaryRefusal.BEARER_MISSING),
        (("Basic secret-current-runtime",), BoundaryRefusal.BEARER_MALFORMED),
        (("Bearer secret current runtime",), BoundaryRefusal.BEARER_MALFORMED),
        (("Bearer stale-runtime",), BoundaryRefusal.BEARER_INVALID),
        (
            ("Bearer secret-current-runtime", "Bearer secret-current-runtime"),
            BoundaryRefusal.BEARER_MULTIPLE,
        ),
    ],
)
def test_mutation_refuses_invalid_bearer_without_disclosing_candidate(
    authorizations: tuple[str, ...], refusal: BoundaryRefusal
) -> None:
    # Arrange
    authorization_headers = tuple(
        ("authorization", authorization) for authorization in authorizations
    )
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        bearer="secret-current-runtime",
        operator_id="local-operator",
    )
    headers = _headers(
        ("host", "localhost:8080"),
        ("origin", "http://localhost:8080"),
        *authorization_headers,
    )

    # Act
    with pytest.raises(BoundaryError) as captured:
        boundary.authorize(headers, mutation=True)

    # Assert
    assert captured.value.refusal is refusal
    assert "secret-current-runtime" not in str(captured.value)
    assert "stale-runtime" not in str(captured.value)


def test_valid_mutation_derives_operator_only_after_current_bearer() -> None:
    # Arrange
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080", "127.0.0.1:8080"),
        allowed_origin="http://localhost:8080",
        bearer="secret-current-runtime",
        operator_id="local-operator",
    )
    headers = _headers(
        ("host", "LOCALHOST:8080"),
        ("origin", "http://LOCALHOST:8080"),
        ("authorization", "Bearer secret-current-runtime"),
    )

    # Act
    authorization = boundary.authorize(headers, mutation=True)

    # Assert
    assert authorization.operator_id == "local-operator"


def test_read_only_request_requires_only_allowlisted_host() -> None:
    # Arrange
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        bearer="secret-current-runtime",
        operator_id="local-operator",
    )

    # Act
    authorization = boundary.authorize(_headers(("host", "localhost:8080")), mutation=False)

    # Assert
    assert authorization.operator_id is None


@pytest.mark.parametrize(
    ("allowed_hosts", "bearer", "operator_id", "message"),
    [
        ((), "bearer", "operator", "at least one allowed Host is required"),
        (
            ("localhost:8080",),
            "",
            "operator",
            "bearer and operator identity must be non-empty",
        ),
        (
            ("localhost:8080",),
            "bearer",
            "",
            "bearer and operator identity must be non-empty",
        ),
    ],
)
def test_boundary_refuses_incomplete_authority_configuration(
    allowed_hosts: tuple[str, ...], bearer: str, operator_id: str, message: str
) -> None:
    # Arrange
    origin = "http://localhost:8080"

    # Act
    with pytest.raises(ValueError, match=message) as captured:
        LocalOperatorBoundary(
            allowed_hosts=allowed_hosts,
            allowed_origin=origin,
            bearer=bearer,
            operator_id=operator_id,
        )

    # Assert
    assert str(captured.value) == message


@pytest.mark.parametrize(
    ("name", "value", "refusal"),
    [
        ("host", b"localhost:\xff", BoundaryRefusal.HOST_MALFORMED),
        ("origin", b"http://localhost:\xff", BoundaryRefusal.ORIGIN_MALFORMED),
        ("authorization", b"Bearer \xff", BoundaryRefusal.BEARER_MALFORMED),
    ],
)
def test_boundary_refuses_non_ascii_security_headers(
    name: str, value: bytes, refusal: BoundaryRefusal
) -> None:
    # Arrange
    boundary = LocalOperatorBoundary(
        allowed_hosts=("localhost:8080",),
        allowed_origin="http://localhost:8080",
        bearer="current",
        operator_id="operator",
    )
    headers = {
        "host": b"localhost:8080",
        "origin": b"http://localhost:8080",
        "authorization": b"Bearer current",
    }
    headers[name] = value
    raw_headers = tuple((key.encode("ascii"), item) for key, item in headers.items())

    # Act
    with pytest.raises(BoundaryError) as captured:
        boundary.authorize(raw_headers, mutation=True)

    # Assert
    assert captured.value.refusal is refusal


@pytest.mark.parametrize(
    "configured_value",
    ["", "local host:8080", "user@localhost:8080", "localhost:bad-port"],
)
def test_boundary_refuses_malformed_configured_hosts(configured_value: str) -> None:
    # Arrange
    expected_fragment = "authority"

    # Act
    with pytest.raises(ValueError, match=expected_fragment) as captured:
        LocalOperatorBoundary(
            allowed_hosts=(configured_value,),
            allowed_origin="http://localhost:8080",
            bearer="current",
            operator_id="operator",
        )

    # Assert
    assert expected_fragment in str(captured.value)


@pytest.mark.parametrize(
    "configured_origin",
    ["", "null", "http://localhost", "http://localhost:bad-port"],
)
def test_boundary_refuses_malformed_configured_origins(configured_origin: str) -> None:
    # Arrange
    expected_fragment = "origin"

    # Act
    with pytest.raises(ValueError, match=expected_fragment) as captured:
        LocalOperatorBoundary(
            allowed_hosts=("localhost:8080",),
            allowed_origin=configured_origin,
            bearer="current",
            operator_id="operator",
        )

    # Assert
    assert expected_fragment in str(captured.value)
