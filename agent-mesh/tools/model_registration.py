"""Register the locked local model in the Platform service's registry (docs/adr/0222).

The Platform service seeds ``general`` and ``planning`` as placeholder rows whose provider and
model name are a sentinel the API returns as null and the Models tab renders as "Not configured".
This replaces them with the model ``model-lock.toml`` already pins, so ADR-0063's digest-locked
identifier stays the one place a model is chosen and the registry is derived from it rather than
typed into a browser.

The identifier is written verbatim, and that is the load-bearing detail. The registry prepends
``ollama/`` to a stored model name containing no ``/``, and ``ollama/…`` is LiteLLM's
``/api/generate`` route, which carries no tool support. The lock's ``ollama_chat/`` prefix keeps the
``/api/chat`` route the coordinator needs, so an identifier that would lose it is refused rather
than rewritten -- that rewrite is the failure ADR-0200 and ADR-0220 exist about.

The endpoint recorded on the row is the *container's* view of Ollama, because the Platform service
and the agents dereference it from inside the container. It is deliberately not the loopback
address the host uses.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import tomllib
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Final, TextIO, cast
from urllib.parse import urlsplit

DEFAULT_ALIASES: Final = ("general", "planning")
"""The two reserved aliases the Platform seeder always creates and never deletes."""

OLLAMA_PROVIDER: Final = "ollama"
DEFAULT_REGISTRY: Final = "http://127.0.0.1:8001"
DEFAULT_API_BASE: Final = "http://host.docker.internal:11434"
"""Ollama as the container sees it; the host's loopback address is unreachable from inside."""

DEFAULT_LOCK: Final = "model-lock.toml"
MODELS_PATH: Final = "/api/v1/platform/models"
PROVIDER_SEPARATOR: Final = "/"
PERMITTED_SCHEMES: Final = frozenset(("http", "https"))
"""The registry is named by an argument, so a `file:` or custom scheme is refused, not opened."""
TIMEOUT_SECONDS: Final = 15.0


class RegistrationRefusal(Enum):
    """Why a model cannot be registered as locked."""

    NO_MODEL = "the lock names no model identifier"
    UNROUTED_IDENTIFIER = (
        "the locked identifier carries no provider route, so the registry would rewrite it to the "
        "completion route, which carries no tool support"
    )
    REGISTRY_UNREACHABLE = "the Platform registry did not answer"
    MALFORMED_REGISTRY = "the Platform registry answered with something other than a model list"
    UNSUPPORTED_SCHEME = "the Platform registry must be named over http or https"
    ABSENT_ALIAS = "the Platform registry holds no such alias"


class RegistrationError(ValueError):
    """A refusal carrying the rule and, where it is not a credential, the value."""

    def __init__(self, refusal: RegistrationRefusal, value: str | None = None) -> None:
        """Create a refusal naming the rule, and the offending value when it is safe to name."""
        super().__init__(refusal.value if value is None else f"{refusal.value}: {value}")
        self.refusal = refusal


def locked_identifier(lock: Path) -> str:
    """Return the one locked model identifier, refusing a lock that cannot supply a routed one."""
    try:
        document = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as failure:
        raise RegistrationError(RegistrationRefusal.NO_MODEL, str(lock)) from failure
    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise RegistrationError(RegistrationRefusal.NO_MODEL, str(lock))
    first = cast("dict[str, object]", models[0])
    identifier = first.get("identifier")
    if not isinstance(identifier, str) or not identifier:
        raise RegistrationError(RegistrationRefusal.NO_MODEL, str(lock))
    if PROVIDER_SEPARATOR not in identifier:
        raise RegistrationError(RegistrationRefusal.UNROUTED_IDENTIFIER, identifier)
    return identifier


def _connection(registry: str) -> http.client.HTTPConnection:
    """Return a connection to the registry, refusing a scheme that is not the web's.

    ``http.client`` rather than ``urllib.request`` for the same reason ``packages/broker``'s SEMP
    transport and the live phase-0 suite give: the connection is explicit, and a URL naming a
    ``file:`` or custom scheme is refused here instead of being opened.
    """
    parts = urlsplit(registry)
    if parts.scheme not in PERMITTED_SCHEMES or not parts.hostname:
        raise RegistrationError(RegistrationRefusal.UNSUPPORTED_SCHEME, registry)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(parts.hostname, parts.port, timeout=TIMEOUT_SECONDS)
    return http.client.HTTPConnection(parts.hostname, parts.port, timeout=TIMEOUT_SECONDS)


def _send(registry: str, path: str, *, method: str, body: object | None = None) -> object:
    """Perform one registry request, converting every transport failure into one refusal."""
    connection = _connection(registry)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json"}
    try:
        connection.request(method, path, body=payload, headers=headers)
        with connection.getresponse() as response:
            return json.loads(response.read() or b"null")
    except (OSError, ValueError) as failure:
        raise RegistrationError(RegistrationRefusal.REGISTRY_UNREACHABLE, registry) from failure
    finally:
        connection.close()


def registered_aliases(registry: str) -> dict[str, str]:
    """Return the registry's alias-to-identifier map, refusing an answer it cannot read."""
    answer = _send(registry, MODELS_PATH, method="GET")
    if not isinstance(answer, dict):
        raise RegistrationError(RegistrationRefusal.MALFORMED_REGISTRY, registry)
    rows = cast("dict[str, object]", answer).get("data")
    if not isinstance(rows, list):
        raise RegistrationError(RegistrationRefusal.MALFORMED_REGISTRY, registry)
    found: dict[str, str] = {}
    for row in cast("list[object]", rows):
        if not isinstance(row, dict):
            continue
        typed = cast("dict[str, object]", row)
        alias, identity = typed.get("alias"), typed.get("id")
        if isinstance(alias, str) and isinstance(identity, str):
            found[alias] = identity
    return found


def register(registry: str, identifier: str, api_base: str, aliases: Sequence[str]) -> None:
    """Point each alias at the locked model, refusing an alias the registry does not hold."""
    held = registered_aliases(registry)
    body = {
        "provider": OLLAMA_PROVIDER,
        "modelName": identifier,
        "apiBase": api_base,
        "authConfig": {"type": "none"},
    }
    for alias in aliases:
        identity = held.get(alias)
        if identity is None:
            raise RegistrationError(RegistrationRefusal.ABSENT_ALIAS, alias)
        _send(registry, f"{MODELS_PATH}/{identity}", method="PATCH", body=body)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.model_registration",
        description="Register the locked local model in the Platform service (docs/adr/0222).",
    )
    parser.add_argument("--lock", default=DEFAULT_LOCK)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--alias", action="append", default=[])
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Register the locked model and return a process exit status.

    Args:
        argv: Command-line arguments, or ``None`` to read them from the process.
        out: Where the summary is written.
        error: Where a refusal is written.

    Returns:
        ``0`` once every named alias points at the locked model, ``1`` on any refusal.
    """
    arguments = _parse(argv)
    aliases = tuple(arguments.alias) or DEFAULT_ALIASES
    try:
        identifier = locked_identifier(Path(arguments.lock))
        register(arguments.registry, identifier, arguments.api_base, aliases)
    except RegistrationError as failure:
        error.write(f"FAILED: {failure}\n")
        return 1
    out.write(f"registered: {identifier} at {arguments.api_base} for {', '.join(aliases)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
