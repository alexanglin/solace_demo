"""Per-process dashboard runtime identity tests."""

from __future__ import annotations

import pytest
from aerial_rescue_dashboard_api.runtime_context import new_runtime_context


def test_runtime_context_uses_256_fresh_random_bits_without_persisting_them() -> None:
    # Arrange
    calls: list[int] = []

    def random_bytes(size: int) -> bytes:
        calls.append(size)
        return bytes(range(size))

    # Act
    context = new_runtime_context(
        random_bytes=random_bytes,
        runtime_id="runtime-test-0001",
        operator_id="local-operator",
    )

    # Assert
    assert calls == [32]
    assert context.runtime_id == "runtime-test-0001"
    assert context.operator_id == "local-operator"
    assert context.bearer == "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


def test_runtime_context_rejects_a_random_source_with_the_wrong_length() -> None:
    # Arrange
    def short_random_bytes(_size: int) -> bytes:
        return b"too-short"

    # Act
    try:
        new_runtime_context(
            random_bytes=short_random_bytes,
            runtime_id="runtime-test-0001",
            operator_id="local-operator",
        )
    except ValueError as error:
        refusal = str(error)
    else:
        refusal = ""

    # Assert
    assert refusal == "runtime bearer source must return exactly 32 bytes"


@pytest.mark.parametrize(
    ("runtime_id", "operator_id"),
    [("Runtime-Uppercase", "local-operator"), ("runtime-valid", "operator identity")],
)
def test_runtime_context_refuses_invalid_public_identities(
    runtime_id: str, operator_id: str
) -> None:
    # Arrange
    expected = "runtime and operator identities must satisfy the identifier contract"

    # Act
    with pytest.raises(ValueError, match=expected) as captured:
        new_runtime_context(runtime_id=runtime_id, operator_id=operator_id)

    # Assert
    assert str(captured.value) == expected
