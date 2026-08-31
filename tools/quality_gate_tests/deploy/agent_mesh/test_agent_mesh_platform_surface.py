"""Whether the browser can actually reach the Platform service's model registry.

Three values in two configuration files have to agree before the Web UI's Models tab renders
anything, and nothing binds them today. They are easy to get individually right and collectively
wrong, because each looks correct in isolation:

- The gateway publishes ``app_config.platform_service.url`` to the browser as
  ``frontend_platform_server_url``, and the frontend concatenates it verbatim onto
  ``/api/v1/platform/models``. Left unset it is the empty string, so the fetch silently becomes a
  same-origin request the gateway mounts no router for, and the tab reports a load error rather
  than an absent registry.
- That URL is dereferenced by the *browser*, not by the container, so it must name the published
  loopback port rather than a container-internal host.
- The page is served from the Web UI's origin and the API answers on the Platform's, so the
  Platform's allowed origins must contain the *Web UI's* origin. Its own origin is never sent:
  the API is fetched from, never loaded as a page. The upstream auto-trusted fallback cannot
  cover this either, because the container sets ``FASTAPI_HOST`` to all interfaces and the
  fallback then constructs an origin no browser sends.
"""

from __future__ import annotations

import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

CONFIG_ROOT = REPOSITORY_ROOT / "agent-mesh" / "configs"
TEMPLATE = REPOSITORY_ROOT / ".env.example"
WEB_UI = "web-ui.yaml"
PLATFORM = "platform.yaml"
PLATFORM_URL_VARIABLE = "PLATFORM_SERVICE_URL"
LOOPBACK = "http://127.0.0.1"


def _app_config(basename: str) -> dict[str, object]:
    """Return the single ``app_config`` of one committed configuration file."""
    document = yaml.safe_load((CONFIG_ROOT / basename).read_text(encoding="utf-8"))
    return cast("dict[str, object]", document["apps"][0]["app_config"])


def _declarations() -> dict[str, str]:
    """Return every assignment in the environment template."""
    pairs = (
        line.partition("=")
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, separator, value in pairs if separator}


def _origin(basename: str) -> str:
    """Return the loopback origin a page served by one app is fetched from."""
    return f"{LOOPBACK}:{_app_config(basename)['fastapi_port']}"


class PlatformSurfaceTests(QualityGateTestCase):
    def test_the_web_ui_names_the_platform_service_through_the_environment(self) -> None:
        # Arrange
        expected = f"${{{PLATFORM_URL_VARIABLE}}}"

        # Act
        platform_service = cast("dict[str, object]", _app_config(WEB_UI).get("platform_service"))

        # Assert
        self.assertIsNotNone(platform_service, "the Models tab has no registry to call")
        self.assertEqual(expected, platform_service["url"])

    def test_the_declared_platform_url_is_the_platform_app_s_published_loopback_port(self) -> None:
        # Arrange
        declared = _declarations()[PLATFORM_URL_VARIABLE]

        # Act
        expected = _origin(PLATFORM)

        # Assert
        self.assertEqual(expected, declared)
        self.assertFalse(declared.endswith("/"), "the frontend concatenates this value verbatim")

    def test_the_platform_admits_the_origin_the_page_is_served_from(self) -> None:
        # Arrange
        page = _origin(WEB_UI)

        # Act
        origins = cast("list[str]", _app_config(PLATFORM)["cors_allowed_origins"])

        # Assert
        self.assertIn(page, origins)

    def test_the_platform_does_not_rely_on_admitting_its_own_origin(self) -> None:
        # Arrange
        api = _origin(PLATFORM)

        # Act
        origins = cast("list[str]", _app_config(PLATFORM)["cors_allowed_origins"])

        # Assert
        self.assertNotIn(api, origins, "a browser never loads the API as a page")


if __name__ == "__main__":
    unittest.main()
