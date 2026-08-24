"""Whether the preflight refuses to start the Agent Mesh when the locked model is not served.

The mesh joined the default profile, recorded in
[ADR-0098](../../../docs/adr/0098-start-the-agent-mesh-with-the-default-profile.md), so ``just up``
starts it on every run. Nothing in the container's readiness path touches Ollama, so
without this check a stopped daemon produces a healthy container whose first prompt fails. This is
the readiness half of the digest comparison
([ADR-0063](../../../docs/adr/0063-lock-local-models-by-manifest-digest.md)) that the offline
configuration validator cannot perform.

The daemon is stubbed on loopback rather than mocked, so the script's real ``curl`` path is the one
under test. No case reaches the network.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import override

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCRIPT = REPOSITORY_ROOT / "scripts" / "preflight-ollama.sh"
IDENTIFIER = "ollama_chat/qwen3:4b"
NAME = "qwen3:4b"
DIGEST = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
OTHER_DIGEST = "0000000000000000000000000000000000000000000000000000000000000000"
UNUSED_PORT = 1
"""Port 1 is privileged and unbound, so a request to it fails without leaving the host."""
SHUTDOWN_SECONDS = 5
"""Bound on joining the stub's thread, so a wedged server cannot outlive its test."""


def _lock(identifier: str, digest: str) -> str:
    """Return a model lock in the canonical form the offline validator enforces."""
    return f'format = 1\n\n[[models]]\nidentifier = "{identifier}"\ndigest = "{digest}"\n'


def _tags(name: str, digest: str) -> bytes:
    """Return an Ollama ``/api/tags`` body in the shape the daemon reports."""
    return json.dumps(
        {"models": [{"name": name, "model": name, "size": 2497293931, "digest": digest}]},
        separators=(",", ":"),
    ).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    body = b"{}"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub silent; its output is not the subject of any assertion."""


class OllamaPreflightTests(QualityGateTestCase):
    def serve(self, body: bytes) -> str:
        """Serve ``body`` on loopback for the duration of the test and return its base URL."""
        handler = type("_Bound", (_Handler,), {"body": body})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            """Stop the loop, join its thread, then close the listening socket.

            All three are required in this order. A socket left to finalization raises in the
            garbage collector, which this suite escalates to a failure.
            """
            server.shutdown()
            thread.join(SHUTDOWN_SECONDS)
            server.server_close()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    def preflight(self, lock: str, endpoint: str) -> tuple[int, str, str]:
        """Run the preflight against ``endpoint`` with ``lock`` as its model lock."""
        repository = self.temporary_repository()
        (repository / "model-lock.toml").write_text(lock, encoding="utf-8")
        result = self.run_script(
            SCRIPT,
            repository,
            (),
            {
                "OLLAMA_PREFLIGHT_MODEL_LOCK": "model-lock.toml",
                "OLLAMA_PREFLIGHT_URL": endpoint,
            },
        )
        return result.returncode, result.stdout, result.stderr

    def test_a_served_model_at_the_locked_digest_is_accepted(self) -> None:
        # Arrange
        endpoint = self.serve(_tags(NAME, DIGEST))

        # Act
        code, stdout, stderr = self.preflight(_lock(IDENTIFIER, f"sha256:{DIGEST}"), endpoint)

        # Assert
        self.assertEqual(0, code, stderr)
        self.assertIn(NAME, stdout)

    def test_an_unreachable_daemon_is_refused_and_names_the_command_that_starts_it(self) -> None:
        # Arrange
        endpoint = f"http://127.0.0.1:{UNUSED_PORT}"

        # Act
        code, _, stderr = self.preflight(_lock(IDENTIFIER, f"sha256:{DIGEST}"), endpoint)

        # Assert
        self.assertNotEqual(0, code)
        self.assertIn("ollama serve", stderr)

    def test_a_model_the_daemon_does_not_serve_is_refused_and_names_the_pull(self) -> None:
        # Arrange
        endpoint = self.serve(_tags("llama3:8b", OTHER_DIGEST))

        # Act
        code, _, stderr = self.preflight(_lock(IDENTIFIER, f"sha256:{DIGEST}"), endpoint)

        # Assert
        self.assertNotEqual(0, code)
        self.assertIn(f"ollama pull {NAME}", stderr)

    def test_a_moved_tag_is_refused_and_reports_both_digests(self) -> None:
        # Arrange
        endpoint = self.serve(_tags(NAME, OTHER_DIGEST))

        # Act
        code, _, stderr = self.preflight(_lock(IDENTIFIER, f"sha256:{DIGEST}"), endpoint)

        # Assert
        self.assertNotEqual(0, code)
        self.assertIn(DIGEST, stderr)

    def test_a_missing_model_lock_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_script(
            SCRIPT, repository, (), {"OLLAMA_PREFLIGHT_MODEL_LOCK": "absent.toml"}
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("absent.toml", result.stderr)

    def test_an_unknown_argument_is_refused(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_script(SCRIPT, repository, ("--unknown",))

        # Assert
        self.assertEqual(2, result.returncode)
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
