"""Whether the registered model is the locked one, and still the tool-capable route.

The Platform service seeds ``general`` and ``planning`` as placeholders whose provider and model
name are a sentinel the API returns as null, which the Models tab renders as "Not configured".
Registration replaces them, and it is derived from ``model-lock.toml`` rather than typed into the
UI, so ADR-0063's digest-locked identifier stays the one place a model is chosen (ADR-0222).

One detail carries the whole point. The Platform service prepends ``ollama/`` to a stored model
name containing no ``/``, and ``ollama/…`` is LiteLLM's ``/api/generate`` route, which has no tool
support. Storing the lock's identifier verbatim keeps the ``ollama_chat/`` prefix and therefore the
``/api/chat`` route the coordinator needs -- the failure ADR-0200 and ADR-0220 were written about.
An identifier that would lose its prefix is refused rather than rewritten.

The registry is stubbed on loopback rather than mocked, so the module's real HTTP path is under
test and no case reaches a container.
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar, override

from tools.model_registration import DEFAULT_ALIASES, OLLAMA_PROVIDER, main

IDENTIFIER = "ollama_chat/llama3.1:8b"
DIGEST = "sha256:" + "4" * 64
API_BASE = "http://host.docker.internal:11434"
SHUTDOWN_SECONDS = 5


def _lock(identifier: str) -> str:
    """Return a model lock in the canonical form the offline validator enforces."""
    return (
        "format = 1\n\n[[models]]\n"
        f'identifier = "{identifier}"\n'
        f'digest = "{DIGEST}"\n'
        'recorded_on = "2026-08-31"\n'
        'recorded_by = "tests"\n'
        'reason = "a reason long enough for the lock"\n'
    )


class _Registry(BaseHTTPRequestHandler):
    """A Platform registry holding the two aliases its seeder always creates."""

    patched: ClassVar[list[tuple[str, dict[str, object]]]] = []
    reject_writes: ClassVar[bool] = False

    def _respond(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        rows = [
            {"id": f"id-of-{alias}", "alias": alias, "provider": None, "modelName": None}
            for alias in DEFAULT_ALIASES
        ]
        self._respond(json.dumps({"data": rows}, separators=(",", ":")).encode("utf-8"))

    def do_PATCH(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).patched.append((self.path, body))
        if type(self).reject_writes:
            self._respond(b'{"detail":"refused"}', 500)
            return
        self._respond(b'{"data":{}}')

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub silent; its output is not the subject of any assertion."""


class ModelRegistrationTests(unittest.TestCase):
    def serve(
        self, *, reject_writes: bool = False
    ) -> tuple[str, list[tuple[str, dict[str, object]]]]:
        """Serve a registry on loopback for the test and return its URL and its writes."""
        patched: list[tuple[str, dict[str, object]]] = []
        handler = type("_Bound", (_Registry,), {"patched": patched, "reject_writes": reject_writes})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            """Stop the loop, join its thread, then close the listening socket."""
            server.shutdown()
            thread.join(SHUTDOWN_SECONDS)
            server.server_close()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}", patched

    def lock_path(self, identifier: str) -> Path:
        """Return a model lock on disk naming ``identifier``."""
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "model-lock.toml"
        path.write_text(_lock(identifier), encoding="utf-8")
        return path

    def register(self, lock: Path, registry: str) -> tuple[int, str, str]:
        """Run the console against ``registry`` with ``lock`` as its model lock."""
        out, error = io.StringIO(), io.StringIO()
        status = main(
            ["--lock", str(lock), "--registry", registry, "--api-base", API_BASE],
            out=out,
            error=error,
        )
        return status, out.getvalue(), error.getvalue()

    def test_both_default_aliases_receive_the_locked_identifier_verbatim(self) -> None:
        # Arrange
        registry, patched = self.serve()

        # Act
        status, out, error = self.register(self.lock_path(IDENTIFIER), registry)

        # Assert
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(len(DEFAULT_ALIASES), len(patched))
        for path, body in patched:
            with self.subTest(path=path):
                self.assertEqual(IDENTIFIER, body["modelName"])
                self.assertEqual(OLLAMA_PROVIDER, body["provider"])
                self.assertEqual(API_BASE, body["apiBase"])
        self.assertIn(IDENTIFIER, out)

    def test_an_identifier_that_would_lose_its_provider_route_is_refused(self) -> None:
        # Arrange
        registry, patched = self.serve()

        # Act
        status, _, error = self.register(self.lock_path("llama3.1:8b"), registry)

        # Assert
        self.assertEqual((1, []), (status, patched))
        self.assertTrue(error.startswith("FAILED:"), error)

    def test_a_lock_naming_no_model_is_refused(self) -> None:
        # Arrange
        registry, patched = self.serve()
        directory = self.enterContext(tempfile.TemporaryDirectory())
        empty = Path(directory) / "model-lock.toml"
        empty.write_text("format = 1\n", encoding="utf-8")

        # Act
        status, _, error = self.register(empty, registry)

        # Assert
        self.assertEqual((1, []), (status, patched))
        self.assertTrue(error.startswith("FAILED:"), error)

    def test_an_unreachable_registry_fails_closed(self) -> None:
        # Arrange
        unreachable = "http://127.0.0.1:1"

        # Act
        status, _, error = self.register(self.lock_path(IDENTIFIER), unreachable)

        # Assert
        self.assertEqual(1, status)
        self.assertTrue(error.startswith("FAILED:"), error)

    def test_a_rejected_write_is_not_reported_as_a_registration(self) -> None:
        """http.client raises nothing on 5xx, so an unchecked status reports success falsely."""
        # Arrange
        registry, patched = self.serve(reject_writes=True)

        # Act
        status, out, error = self.register(self.lock_path(IDENTIFIER), registry)

        # Assert
        self.assertEqual((1, ""), (status, out))
        self.assertTrue(error.startswith("FAILED:"), error)
        self.assertEqual(1, len(patched), "it stops at the first rejected write")

    def test_registering_twice_leaves_the_same_registered_model(self) -> None:
        # Arrange
        registry, patched = self.serve()
        lock = self.lock_path(IDENTIFIER)

        # Act
        first, _, _ = self.register(lock, registry)
        second, _, _ = self.register(lock, registry)

        # Assert
        self.assertEqual((0, 0), (first, second))
        self.assertEqual({IDENTIFIER}, {body["modelName"] for _, body in patched})

    def test_an_alias_the_registry_does_not_hold_is_refused(self) -> None:
        # Arrange
        registry, patched = self.serve()
        out, error = io.StringIO(), io.StringIO()
        arguments = [
            "--lock",
            str(self.lock_path(IDENTIFIER)),
            "--registry",
            registry,
            "--alias",
            "absent-alias",
        ]

        # Act
        status = main(arguments, out=out, error=error)

        # Assert
        self.assertEqual((1, []), (status, patched))
        self.assertIn("absent-alias", error.getvalue())


if __name__ == "__main__":
    unittest.main()
