"""Whether the Agent Mesh namespace terminates so upstream's unseparated topics stay granted.

Solace Agent Mesh builds most of its topics through helpers that normalise the namespace --
``get_a2a_base_topic`` is ``f"{namespace.rstrip('/')}/{A2A_BASE_PATH}"``. Two families do not.
``agent/adk/models/dynamic_model_provider_topics.py`` and the Web UI's scheduler topics
concatenate the namespace directly against the next level, so a namespace with no trailing
separator yields a topic whose *first level* is the namespace glued to a word.

That is not cosmetic here. ``docs/adr/0061``'s matrix grants the Agent Mesh roles exactly
``a2a_subscription(NAMESPACE)``, and a glued first level falls outside it, so every model
configuration message would be denied by the broker rather than delivered. Terminating the
namespace the mesh's own configuration declares moves those families back inside the grant and
leaves every normalising helper's output byte-identical, because they strip the separator
before adding their own (``docs/adr/0221``).

The declared value in ``.env.example`` is deliberately not the place this is fixed: it feeds
``just provision --namespace`` and ``a2a_subscription``, both of which refuse an empty level.
"""

from __future__ import annotations

import unittest
from typing import cast

import yaml
from aerial_rescue_broker.subscriptions import a2a_subscription

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

CONFIG_ROOT = REPOSITORY_ROOT / "agent-mesh" / "configs"
TEMPLATE = REPOSITORY_ROOT / ".env.example"
NAMESPACE_KEY = "NAMESPACE"
NAMESPACE_REFERENCE = "${NAMESPACE}"
LEVEL_SEPARATOR = "/"
MULTI_LEVEL_WILDCARD = ">"

UPSTREAM_TOPIC_FORMATS = (
    "{namespace}configuration/model/bootstrap/>",
    "{namespace}configuration/model/bootstrap/{model_id}",
    "{namespace}configuration/model/response/{model_id}/{component_id}",
    "{namespace}configuration/model/{model_id}",
)
"""The pinned wheel's ``dynamic_model_provider_topics`` constants, copied verbatim."""

UPSTREAM_QUEUE_FORMATS = (
    "{namespace}q/model_config/{component_id}",
    "{namespace}q/platform/model_config_bootstrap",
)
"""The endpoint names the same two components create, built the same unseparated way."""

MODEL_ID = "aerial-rescue-local"
COMPONENT_ID = "MissionCoordinator"


def _declared_namespace() -> str:
    """Return the namespace value the environment template fixes."""
    pairs = (
        line.partition("=")
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, separator, value in pairs if separator}[NAMESPACE_KEY]


def _configured_namespaces() -> dict[str, str]:
    """Return the ``namespace`` every committed app declares, keyed by file name."""
    declared: dict[str, str] = {}
    for path in sorted(CONFIG_ROOT.glob("*.yaml")):
        document = cast("dict[str, object]", yaml.safe_load(path.read_text(encoding="utf-8")))
        for app in cast("list[dict[str, object]]", document["apps"]):
            config = cast("dict[str, object]", app["app_config"])
            declared[f"{path.name}:{app['name']}"] = cast("str", config["namespace"])
    return declared


def _rendered(template: str, namespace: str) -> str:
    """Return one upstream format rendered for the namespace the deployment resolves."""
    return template.format(namespace=namespace, model_id=MODEL_ID, component_id=COMPONENT_ID)


def _covers(subscription: str, topic: str) -> bool:
    """Whether a multi-level subscription admits a topic."""
    return topic.startswith(subscription.removesuffix(MULTI_LEVEL_WILDCARD))


class NamespaceDeclarationTests(QualityGateTestCase):
    def test_every_committed_app_terminates_the_namespace_it_declares(self) -> None:
        # Arrange
        expected = f"{NAMESPACE_REFERENCE}{LEVEL_SEPARATOR}"

        # Act
        declared = _configured_namespaces()

        # Assert
        self.assertNotEqual({}, declared, "no committed app declares a namespace")
        for location, namespace in declared.items():
            with self.subTest(location=location):
                self.assertEqual(expected, namespace)


class ModelConfigurationTopicAuthorityTests(QualityGateTestCase):
    def test_every_model_configuration_topic_falls_inside_the_a2a_grant(self) -> None:
        # Arrange
        declared = _declared_namespace()
        resolved = f"{declared}{LEVEL_SEPARATOR}"
        subscription = a2a_subscription(declared)

        # Act
        topics = tuple(_rendered(each, resolved) for each in UPSTREAM_TOPIC_FORMATS)

        # Assert
        for topic in topics:
            with self.subTest(topic=topic):
                self.assertTrue(_covers(subscription, topic), f"{topic} is outside {subscription}")

    def test_the_unterminated_namespace_would_glue_the_first_level_and_lose_the_grant(
        self,
    ) -> None:
        # Arrange
        declared = _declared_namespace()
        subscription = a2a_subscription(declared)

        # Act
        glued = tuple(_rendered(each, declared) for each in UPSTREAM_TOPIC_FORMATS)

        # Assert
        for topic in glued:
            with self.subTest(topic=topic):
                self.assertFalse(_covers(subscription, topic), f"{topic} unexpectedly granted")


class ModelConfigurationEndpointTests(QualityGateTestCase):
    def test_every_model_configuration_endpoint_carries_a_separated_first_level(self) -> None:
        # Arrange
        declared = _declared_namespace()
        resolved = f"{declared}{LEVEL_SEPARATOR}"

        # Act
        queues = tuple(_rendered(each, resolved) for each in UPSTREAM_QUEUE_FORMATS)

        # Assert
        for queue in queues:
            with self.subTest(queue=queue):
                self.assertEqual(declared, queue.split(LEVEL_SEPARATOR)[0])


if __name__ == "__main__":
    unittest.main()
