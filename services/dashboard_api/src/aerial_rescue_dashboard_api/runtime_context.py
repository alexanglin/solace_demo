"""Ephemeral dashboard process identity and local operator credential."""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_BEARER_BYTES = 32


@dataclass(frozen=True)
class RuntimeContext:
    """One process-lifetime context that callers keep only in memory."""

    runtime_id: str
    operator_id: str
    bearer: str


def new_runtime_context(
    *,
    runtime_id: str,
    operator_id: str,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> RuntimeContext:
    """Mint one bearer from exactly 256 independent random bits."""
    if _IDENTIFIER.fullmatch(runtime_id) is None or _IDENTIFIER.fullmatch(operator_id) is None:
        message = "runtime and operator identities must satisfy the identifier contract"
        raise ValueError(message)
    entropy = random_bytes(_BEARER_BYTES)
    if len(entropy) != _BEARER_BYTES:
        message = "runtime bearer source must return exactly 32 bytes"
        raise ValueError(message)
    bearer = base64.urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")
    return RuntimeContext(runtime_id=runtime_id, operator_id=operator_id, bearer=bearer)
