"""Tests for the fail-closed compose policy gate over compose documents."""

from __future__ import annotations

import contextlib
import io
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from tools import compose_policy_gate
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

DIGEST: Final = "sha256:" + "0" * 64
IMAGE: Final = f"example/service:1.0.0@{DIGEST}"
NAMES: Final = frozenset({"SESSION_SECRET_KEY", "SOLACE_BROKER_PASSWORD"})
OMIT: Final = object()

CONFORMING_DOCKERFILE: Final = (
    f"FROM solace/solace-agent-mesh:1.28.7@{DIGEST} AS plugins\n"
    "USER 0\n"
    "RUN pip install --no-cache-dir --require-hashes -r /tmp/plugins.txt\n"
    "USER 65534\n"
    f"FROM solace/solace-agent-mesh:1.28.7@{DIGEST}\n"
    "COPY --from=plugins /opt/plugins/ /opt/venv/lib/python3.13/site-packages/\n"
)


def merged(base: dict[str, object], overrides: Mapping[str, object]) -> dict[str, object]:
    """Return ``base`` with overrides applied; an ``OMIT`` value deletes the key."""
    result = dict(base)
    for key, value in overrides.items():
        if value is OMIT:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def service(**overrides: object) -> dict[str, object]:
    """Return a conforming generic service."""
    return merged(
        {
            "image": IMAGE,
            "ports": ["127.0.0.1:9443:9443"],
            "healthcheck": {"test": ["CMD", "true"]},
        },
        overrides,
    )


def broker(**overrides: object) -> dict[str, object]:
    """Return a conforming broker service."""
    return merged(
        {
            "image": f"solace/solace-pubsub-standard:10.26.0.8799@{DIGEST}",
            "shm_size": "1g",
            "ulimits": {"core": -1, "nofile": {"soft": 2448, "hard": 1048576}},
            "environment": {
                "username_admin_globalaccesslevel": "admin",
                "username_admin_passwordfilepath": "/run/secrets/broker-admin-password",
                "tls_servercertificate_filepath": "/run/secrets/broker-server.pem",
                "system_scaling_maxconnectioncount": "100",
            },
            "ports": ["127.0.0.1:55443:55443", "127.0.0.1:1943:1943"],
            "healthcheck": {"test": ["CMD-SHELL", "true"]},
        },
        overrides,
    )


def agent_mesh(**overrides: object) -> dict[str, object]:
    """Return a conforming Agent Mesh service built from the reviewed Dockerfile."""
    return merged(
        {
            "build": {"context": "..", "dockerfile": "deploy/agent-mesh/Dockerfile"},
            "image": "aerial-rescue/agent-mesh:1.28.7",
            "profiles": ["mesh"],
            "environment": {
                "SOLACE_DEV_MODE": "false",
                "SESSION_SECRET_KEY": "${SESSION_SECRET_KEY}",
                "SOLACE_BROKER_URL": "tcps://broker:55443",
            },
            "ports": ["127.0.0.1:8000:8000"],
            "healthcheck": {"test": ["CMD", "true"]},
        },
        overrides,
    )


def stack_with(
    text: str = "",
    secrets: Mapping[str, object] | None = None,
) -> compose_policy_gate.ComposeFile:
    """Return the conforming broker and Agent Mesh stack with the given text or secrets."""
    document: dict[str, object] = {"services": {"broker": broker(), "agent-mesh": agent_mesh()}}
    if secrets is not None:
        document["secrets"] = dict(secrets)
    return compose_policy_gate.ComposeFile("deploy/compose.yaml", document, text)


def stack(**services: object) -> compose_policy_gate.ComposeFile:
    """Return a compose file holding a conforming broker and Agent Mesh plus ``services``."""
    document: dict[str, object] = {
        "services": {"broker": broker(), "agent-mesh": agent_mesh(), **services},
    }
    return compose_policy_gate.ComposeFile("deploy/compose.yaml", document, "")


def reviewed_dockerfile() -> compose_policy_gate.Dockerfile:
    """Return the Dockerfile the conforming Agent Mesh service builds."""
    return compose_policy_gate.Dockerfile("deploy/agent-mesh/Dockerfile", CONFORMING_DOCKERFILE)


def diagnostics(
    compose: compose_policy_gate.ComposeFile,
    names: frozenset[str] = NAMES,
    dockerfiles: tuple[compose_policy_gate.Dockerfile, ...] | None = None,
) -> list[str]:
    """Evaluate one compose file with the reviewed Dockerfile unless told otherwise."""
    reviewed = (reviewed_dockerfile(),) if dockerfiles is None else dockerfiles
    return compose_policy_gate.evaluate((compose,), reviewed, names)


class ComposeParsingTests(QualityGateTestCase):
    def test_invalid_yaml_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []

        # Act
        parsed = compose_policy_gate.parse_compose("deploy/compose.yaml", "services: [", errors)

        # Assert
        self.assertIsNone(parsed)
        self.assertTrue(
            any(error.startswith("deploy/compose.yaml: invalid YAML") for error in errors)
        )

    def test_a_non_mapping_document_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []

        # Act
        parsed = compose_policy_gate.parse_compose(
            "deploy/compose.yaml", "- just\n- a list\n", errors
        )

        # Assert
        self.assertIsNone(parsed)
        self.assertIn("deploy/compose.yaml: the compose document must be a mapping", errors)

    def test_a_document_without_a_services_mapping_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []

        # Act
        parsed = compose_policy_gate.parse_compose("deploy/compose.yaml", "services: 3\n", errors)

        # Assert
        self.assertIsNone(parsed)
        self.assertIn(
            "deploy/compose.yaml: services must be a mapping of string names to mappings", errors
        )

    def test_a_service_that_is_not_a_mapping_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []

        # Act
        parsed = compose_policy_gate.parse_compose(
            "deploy/compose.yaml", "services:\n  broker: just-a-string\n", errors
        )

        # Assert
        self.assertIsNone(parsed)
        self.assertIn("deploy/compose.yaml: service 'broker' must be a mapping", errors)

    def test_an_unreadable_compose_file_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []
        missing = self.temporary_directory() / "compose.yaml"

        # Act
        loaded = compose_policy_gate.load_compose(missing, errors)

        # Assert
        self.assertIsNone(loaded)
        self.assertTrue(any(error.startswith(f"{missing}: cannot read:") for error in errors))

    def test_anchors_and_merge_keys_are_resolved_before_rules_run(self) -> None:
        # Arrange
        errors: list[str] = []
        text = f"x-common: &common\n  image: {IMAGE}\nservices:\n  broker:\n    <<: *common\n"

        # Act
        parsed = compose_policy_gate.parse_compose("deploy/compose.yaml", text, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(IMAGE, parsed.services["broker"]["image"])

    def test_a_compose_file_whose_services_are_not_a_mapping_has_no_services(self) -> None:
        # Arrange
        compose = compose_policy_gate.ComposeFile("deploy/compose.yaml", {"services": 3}, "")

        # Act
        services = compose.services

        # Assert
        self.assertEqual({}, dict(services))


class ImagePinningTests(QualityGateTestCase):
    def test_a_tag_and_digest_pinned_image_passes(self) -> None:
        # Arrange
        compose = stack(web=service())

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_an_image_without_a_digest_fails(self) -> None:
        # Arrange
        compose = stack(web=service(image="example/service:1.0.0"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.image must be pinned as name:tag@sha256:<64 hex digits>", findings
        )

    def test_an_image_without_a_tag_fails(self) -> None:
        # Arrange
        compose = stack(web=service(image=f"example/service@{DIGEST}"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.image must be pinned as name:tag@sha256:<64 hex digits>", findings
        )

    def test_a_short_digest_fails(self) -> None:
        # Arrange
        compose = stack(web=service(image="example/service:1.0.0@sha256:abc123"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.image must be pinned as name:tag@sha256:<64 hex digits>", findings
        )

    def test_the_latest_tag_fails_even_with_a_digest(self) -> None:
        # Arrange
        compose = stack(web=service(image=f"example/service:latest@{DIGEST}"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web.image uses the floating tag latest", findings)

    def test_a_service_with_build_and_no_image_passes_the_image_rule(self) -> None:
        # Arrange
        compose = stack(**{"agent-mesh": agent_mesh(image=OMIT)})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_service_with_neither_image_nor_build_fails(self) -> None:
        # Arrange
        compose = stack(web=service(image=OMIT))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web declares neither image nor build", findings)


class PublishedPortTests(QualityGateTestCase):
    def test_a_loopback_short_syntax_port_passes(self) -> None:
        # Arrange
        compose = stack(web=service(ports=["127.0.0.1:9443:9443/tcp"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_loopback_long_syntax_port_passes(self) -> None:
        # Arrange
        compose = stack(
            web=service(ports=[{"host_ip": "127.0.0.1", "published": "9443", "target": 9443}])
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_bare_container_port_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=[9443]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_a_host_container_pair_without_an_address_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=["9443:9443"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_an_all_interfaces_address_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=["0.0.0.0:9443:9443"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_an_ipv6_loopback_address_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=["[::1]:9443:9443"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_a_port_range_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=["127.0.0.1:9443-9444:9443-9444"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_a_long_syntax_port_without_host_ip_fails(self) -> None:
        # Arrange
        compose = stack(web=service(ports=[{"published": 9443, "target": 9443}]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_a_long_syntax_port_with_a_range_fails(self) -> None:
        # Arrange
        compose = stack(
            web=service(ports=[{"host_ip": "127.0.0.1", "published": "9443-9444", "target": 9443}])
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )

    def test_host_network_mode_fails(self) -> None:
        # Arrange
        compose = stack(web=service(network_mode="host"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web.network_mode must not be host", findings)

    def test_a_service_without_ports_passes(self) -> None:
        # Arrange
        compose = stack(web=service(ports=OMIT))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_long_syntax_port_with_a_boolean_fails(self) -> None:
        # Arrange
        compose = stack(
            web=service(ports=[{"host_ip": "127.0.0.1", "published": True, "target": 9443}])
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.ports[0] must be 127.0.0.1:<host>:<container> with single integer ports",
            findings,
        )


class LiteralSecretTests(QualityGateTestCase):
    def test_an_indirected_secret_passes(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment={"SOLACE_BROKER_PASSWORD": "${SOLACE_BROKER_PASSWORD}"})
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_required_indirection_passes(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment={"SOLACE_BROKER_PASSWORD": "${SOLACE_BROKER_PASSWORD:?set it}"})
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_secret_file_path_passes(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment={"POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password"})
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_null_pass_through_passes(self) -> None:
        # Arrange
        compose = stack(web=service(environment={"SOLACE_BROKER_PASSWORD": None}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_literal_secret_in_mapping_form_fails(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment={"SOLACE_BROKER_PASSWORD": "wilderness-demo-password"})
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.environment.SOLACE_BROKER_PASSWORD holds a literal secret; "
            "use ${SOLACE_BROKER_PASSWORD} indirection or a path under /run/secrets/",
            findings,
        )

    def test_a_literal_secret_in_list_form_fails(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment=["SOLACE_BROKER_PASSWORD=wilderness-demo-password"])
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.environment.SOLACE_BROKER_PASSWORD holds a literal secret; "
            "use ${SOLACE_BROKER_PASSWORD} indirection or a path under /run/secrets/",
            findings,
        )

    def test_a_lowercase_secret_name_fails(self) -> None:
        # Arrange
        compose = stack(web=service(environment={"api_key": "wilderness-demo-key"}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.environment.api_key holds a literal secret; "
            "use ${api_key} indirection or a path under /run/secrets/",
            findings,
        )

    def test_a_url_with_userinfo_fails(self) -> None:
        # Arrange
        compose = stack(
            web=service(environment={"DATABASE_URL": "postgresql://alice:demo@postgres/rescue"})
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web.environment.DATABASE_URL embeds credentials in a URL", findings)

    def test_a_non_credential_literal_passes(self) -> None:
        # Arrange
        compose = stack(web=service(environment={"SOLACE_BROKER_VPN": "default"}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_literal_secret_in_build_args_fails(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    build={
                        "context": "..",
                        "dockerfile": "deploy/agent-mesh/Dockerfile",
                        "args": {"PIP_INDEX_TOKEN": "wilderness-demo-token"},
                    }
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.agent-mesh.build.args.PIP_INDEX_TOKEN holds a literal secret; "
            "use ${PIP_INDEX_TOKEN} indirection or a path under /run/secrets/",
            findings,
        )

    def test_a_non_string_secret_value_fails(self) -> None:
        # Arrange
        compose = stack(web=service(environment={"API_KEY": 123456}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.environment.API_KEY holds a literal secret; "
            "use ${API_KEY} indirection or a path under /run/secrets/",
            findings,
        )


class InterpolationTests(QualityGateTestCase):
    def test_a_declared_variable_passes(self) -> None:
        # Arrange
        compose = stack_with(text="environment:\n  KEY: ${SESSION_SECRET_KEY}\n")

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_an_undeclared_variable_fails(self) -> None:
        # Arrange
        compose = stack_with(text="environment:\n  KEY: ${UNDECLARED_NAME}\n")

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "deploy/compose.yaml: ${UNDECLARED_NAME} is not declared in .env.example", findings
        )

    def test_a_variable_with_a_default_is_still_checked(self) -> None:
        # Arrange
        compose = stack_with(text="environment:\n  KEY: ${UNDECLARED_NAME:-fallback}\n")

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "deploy/compose.yaml: ${UNDECLARED_NAME} is not declared in .env.example", findings
        )

    def test_an_escaped_dollar_is_not_a_reference(self) -> None:
        # Arrange
        compose = stack_with(text="command: ['sh', '-c', 'echo $${NOT_A_REFERENCE}']\n")

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_bare_dollar_reference_is_checked(self) -> None:
        # Arrange
        compose = stack_with(text="environment:\n  KEY: $UNDECLARED_NAME\n")

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "deploy/compose.yaml: ${UNDECLARED_NAME} is not declared in .env.example", findings
        )


class HealthcheckTests(QualityGateTestCase):
    def test_a_service_without_a_healthcheck_fails(self) -> None:
        # Arrange
        compose = stack(web=service(healthcheck=OMIT))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web lacks a healthcheck.test", findings)

    def test_a_disabled_healthcheck_fails(self) -> None:
        # Arrange
        compose = stack(web=service(healthcheck={"test": ["CMD", "true"], "disable": True}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web lacks a healthcheck.test", findings)

    def test_a_healthcheck_without_a_test_fails(self) -> None:
        # Arrange
        compose = stack(web=service(healthcheck={"interval": "10s"}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web lacks a healthcheck.test", findings)


class PlatformTests(QualityGateTestCase):
    def test_the_event_management_agent_pinned_to_amd64_passes(self) -> None:
        # Arrange
        compose = stack(
            **{"event-management-agent": service(platform="linux/amd64", profiles=["event-portal"])}
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_the_event_management_agent_without_a_platform_fails(self) -> None:
        # Arrange
        compose = stack(**{"event-management-agent": service(profiles=["event-portal"])})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.event-management-agent.platform must be linux/amd64", findings)

    def test_the_event_management_agent_on_another_platform_fails(self) -> None:
        # Arrange
        compose = stack(
            **{"event-management-agent": service(platform="linux/arm64", profiles=["event-portal"])}
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.event-management-agent.platform must be linux/amd64", findings)

    def test_any_other_service_with_a_platform_fails(self) -> None:
        # Arrange
        compose = stack(web=service(platform="linux/amd64"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web.platform may only be set on: event-management-agent", findings)


class ProfileTests(QualityGateTestCase):
    def test_a_known_profile_passes(self) -> None:
        # Arrange
        compose = stack(web=service(profiles=["services"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_an_unknown_profile_fails(self) -> None:
        # Arrange
        compose = stack(web=service(profiles=["services", "debug"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.web.profiles[1] is not a known profile (known: event-portal, mesh, services)",
            findings,
        )


class IndirectionTests(QualityGateTestCase):
    def test_a_service_using_extends_fails(self) -> None:
        # Arrange
        compose = stack(web=service(extends={"file": "other.yaml", "service": "web"}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.web.extends is not permitted", findings)

    def test_an_inline_dockerfile_fails(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    build={"context": "..", "dockerfile_inline": "FROM scratch\n"}
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.agent-mesh.build.dockerfile_inline is not permitted", findings)

    def test_a_top_level_include_fails(self) -> None:
        # Arrange
        compose = compose_policy_gate.ComposeFile(
            "deploy/compose.yaml",
            {
                "include": ["other.yaml"],
                "services": {"broker": broker(), "agent-mesh": agent_mesh()},
            },
            "",
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("deploy/compose.yaml: top-level include is not permitted", findings)


class SecretDeclarationTests(QualityGateTestCase):
    def test_a_file_secret_under_the_ignored_root_passes(self) -> None:
        # Arrange
        compose = stack_with(secrets={"postgres-password": {"file": "./secrets/postgres-password"}})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_an_environment_sourced_secret_passes(self) -> None:
        # Arrange
        compose = stack_with(secrets={"token": {"environment": "SESSION_SECRET_KEY"}})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_file_secret_outside_the_ignored_root_fails(self) -> None:
        # Arrange
        compose = stack_with(secrets={"postgres-password": {"file": "../postgres-password"}})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "secrets.postgres-password must declare a file under ./secrets/ "
            "or an environment source",
            findings,
        )

    def test_a_secret_without_a_source_fails(self) -> None:
        # Arrange
        compose = stack_with(secrets={"postgres-password": {}})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "secrets.postgres-password must declare a file under ./secrets/ "
            "or an environment source",
            findings,
        )

    def test_a_secret_that_is_not_a_mapping_fails(self) -> None:
        # Arrange
        compose = stack_with(secrets={"postgres-password": "./secrets/postgres-password"})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "secrets.postgres-password must declare a file under ./secrets/ "
            "or an environment source",
            findings,
        )


class BrokerServiceTests(QualityGateTestCase):
    def test_a_conforming_broker_passes(self) -> None:
        # Arrange
        compose = stack()

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_missing_broker_service_fails(self) -> None:
        # Arrange
        compose = compose_policy_gate.ComposeFile(
            "deploy/compose.yaml", {"services": {"agent-mesh": agent_mesh()}}, ""
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn('services must include the broker service "broker"', findings)

    def test_a_broker_without_shm_size_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(shm_size=OMIT))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.broker.shm_size is required", findings)

    def test_a_broker_without_nofile_limits_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(ulimits={"core": -1}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.broker.ulimits.nofile must declare soft and hard limits", findings)

    def test_a_broker_with_only_a_soft_nofile_limit_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(ulimits={"nofile": {"soft": 2448}}))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.broker.ulimits.nofile must declare soft and hard limits", findings)

    def test_a_broker_without_the_certificate_path_fails(self) -> None:
        # Arrange
        compose = stack(
            broker=broker(
                environment={
                    "username_admin_passwordfilepath": "/run/secrets/broker-admin-password"
                }
            )
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.broker.environment must set tls_servercertificate_filepath", findings
        )

    def test_a_broker_that_does_not_publish_tls_smf_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(ports=["127.0.0.1:1943:1943"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.broker.ports must publish container port 55443 (SMF over TLS)", findings
        )

    def test_a_broker_that_publishes_plaintext_smf_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(ports=["127.0.0.1:55443:55443", "127.0.0.1:55554:55555"]))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.broker.ports must not publish container port 55555 (plaintext SMF or SEMP)",
            findings,
        )

    def test_a_broker_that_publishes_plaintext_semp_fails(self) -> None:
        # Arrange
        compose = stack(
            broker=broker(
                ports=[
                    "127.0.0.1:55443:55443",
                    {"host_ip": "127.0.0.1", "published": 18080, "target": 8080},
                ]
            )
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.broker.ports must not publish container port 8080 (plaintext SMF or SEMP)",
            findings,
        )

    def test_a_broker_whose_ulimits_are_not_a_mapping_fails(self) -> None:
        # Arrange
        compose = stack(broker=broker(ulimits="nofile"))

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn("services.broker.ulimits.nofile must declare soft and hard limits", findings)


class AgentMeshServiceTests(QualityGateTestCase):
    def test_a_missing_agent_mesh_service_fails(self) -> None:
        # Arrange
        compose = compose_policy_gate.ComposeFile(
            "deploy/compose.yaml", {"services": {"broker": broker()}}, ""
        )

        # Act
        findings = diagnostics(compose, dockerfiles=())

        # Assert
        self.assertIn('services must include the Agent Mesh service "agent-mesh"', findings)

    def test_dev_mode_absent_fails(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    environment={"SESSION_SECRET_KEY": "${SESSION_SECRET_KEY}"}
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.agent-mesh.environment.SOLACE_DEV_MODE must be explicitly false", findings
        )

    def test_dev_mode_true_fails(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    environment={
                        "SOLACE_DEV_MODE": "true",
                        "SESSION_SECRET_KEY": "${SESSION_SECRET_KEY}",
                    }
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.agent-mesh.environment.SOLACE_DEV_MODE must be explicitly false", findings
        )

    def test_dev_mode_as_a_yaml_boolean_false_passes(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    environment={
                        "SOLACE_DEV_MODE": False,
                        "SESSION_SECRET_KEY": "${SESSION_SECRET_KEY}",
                    }
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_dev_mode_in_list_form_passes(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    environment=[
                        "SOLACE_DEV_MODE=false",
                        "SESSION_SECRET_KEY=${SESSION_SECRET_KEY}",
                    ]
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_missing_session_secret_fails(self) -> None:
        # Arrange
        compose = stack(**{"agent-mesh": agent_mesh(environment={"SOLACE_DEV_MODE": "false"})})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertIn(
            "services.agent-mesh.environment must set SESSION_SECRET_KEY by indirection; "
            "the image default is a placeholder",
            findings,
        )


class BuildReferenceTests(QualityGateTestCase):
    def test_a_build_whose_dockerfile_is_not_under_review_fails(self) -> None:
        # Arrange
        compose = stack()

        # Act
        findings = diagnostics(compose, dockerfiles=())

        # Assert
        self.assertIn(
            "services.agent-mesh.build names deploy/agent-mesh/Dockerfile, "
            "which is not under review",
            findings,
        )

    def test_a_build_with_an_explicit_dockerfile_name_is_resolved_against_the_context(self) -> None:
        # Arrange
        compose = stack(
            **{
                "agent-mesh": agent_mesh(
                    build={"context": "./agent-mesh", "dockerfile": "Dockerfile"}
                )
            }
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)

    def test_a_dockerfile_no_service_builds_fails(self) -> None:
        # Arrange
        compose = stack()
        orphan = compose_policy_gate.Dockerfile(
            "deploy/application/Dockerfile", CONFORMING_DOCKERFILE
        )

        # Act
        findings = diagnostics(compose, dockerfiles=(reviewed_dockerfile(), orphan))

        # Assert
        self.assertIn("deploy/application/Dockerfile is not built by any compose service", findings)

    def test_services_split_across_two_compose_files_are_judged_together(self) -> None:
        # Arrange
        first = compose_policy_gate.ComposeFile(
            "deploy/compose.yaml", {"services": {"broker": broker()}}, ""
        )
        second = compose_policy_gate.ComposeFile(
            "deploy/compose.mesh.yaml", {"services": {"agent-mesh": agent_mesh()}}, ""
        )

        # Act
        findings = compose_policy_gate.evaluate((first, second), (reviewed_dockerfile(),), NAMES)

        # Assert
        self.assertEqual([], findings)

    def test_a_build_given_as_a_context_string_is_resolved(self) -> None:
        # Arrange
        compose = stack(**{"agent-mesh": agent_mesh(build="./agent-mesh")})

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual([], findings)


class CommandLineTests(QualityGateTestCase):
    def write_stack(self, root: Path) -> tuple[Path, Path, Path]:
        """Write a conforming template, compose file, and Dockerfile under ``root``."""
        template = root / ".env.example"
        template.write_text(
            "SESSION_SECRET_KEY=<required>\nSOLACE_BROKER_PASSWORD=\n", encoding="utf-8"
        )
        deploy = root / "deploy" / "agent-mesh"
        deploy.mkdir(parents=True)
        dockerfile = deploy / "Dockerfile"
        dockerfile.write_text(CONFORMING_DOCKERFILE, encoding="utf-8")
        compose = root / "deploy" / "compose.yaml"
        compose.write_text(
            "services:\n"
            "  broker:\n"
            f"    image: solace/solace-pubsub-standard:10.26.0.8799@{DIGEST}\n"
            "    shm_size: 1g\n"
            "    ulimits:\n"
            "      nofile: {soft: 2448, hard: 1048576}\n"
            "    environment:\n"
            "      username_admin_passwordfilepath: /run/secrets/broker-admin-password\n"
            "      tls_servercertificate_filepath: /run/secrets/broker-server.pem\n"
            "    ports: ['127.0.0.1:55443:55443']\n"
            "    healthcheck: {test: ['CMD', 'true']}\n"
            "  agent-mesh:\n"
            "    build: {context: .., dockerfile: deploy/agent-mesh/Dockerfile}\n"
            "    profiles: [mesh]\n"
            "    environment:\n"
            "      SOLACE_DEV_MODE: 'false'\n"
            "      SESSION_SECRET_KEY: ${SESSION_SECRET_KEY}\n"
            "    ports: ['127.0.0.1:8000:8000']\n"
            "    healthcheck: {test: ['CMD', 'true']}\n",
            encoding="utf-8",
        )
        return template, compose, dockerfile

    def run_gate(self, arguments: list[str]) -> tuple[int, str]:
        """Run the gate's command line and capture its standard error."""
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            status = compose_policy_gate.main(arguments)
        return status, captured.getvalue()

    def test_a_conforming_stack_returns_success(self) -> None:
        # Arrange
        root = self.temporary_directory()
        template, compose, dockerfile = self.write_stack(root)

        # Act
        status, output = self.run_gate(
            [
                "--env-template",
                str(template),
                "--compose",
                str(compose),
                "--dockerfile",
                str(dockerfile),
            ]
        )

        # Assert
        self.assertEqual(0, status, output)
        self.assertEqual("", output)

    def test_a_finding_returns_a_blocking_status_with_a_prefixed_diagnostic(self) -> None:
        # Arrange
        root = self.temporary_directory()
        template, compose, dockerfile = self.write_stack(root)
        compose.write_text(
            compose.read_text(encoding="utf-8").replace("127.0.0.1:8000:8000", "8000:8000"),
            encoding="utf-8",
        )

        # Act
        status, output = self.run_gate(
            [
                "--env-template",
                str(template),
                "--compose",
                str(compose),
                "--dockerfile",
                str(dockerfile),
            ]
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn(
            "COMPOSE: services.agent-mesh.ports[0] must be 127.0.0.1:<host>:<container>", output
        )

    def test_no_compose_file_returns_a_blocking_status(self) -> None:
        # Arrange
        root = self.temporary_directory()
        template, _, _ = self.write_stack(root)

        # Act
        status, output = self.run_gate(["--env-template", str(template)])

        # Assert
        self.assertEqual(1, status)
        self.assertIn(
            "COMPOSE: no compose file was given; the gate cannot admit an empty stack", output
        )

    def test_a_missing_template_returns_a_blocking_status(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _, compose, dockerfile = self.write_stack(root)
        missing = root / "absent.env.example"

        # Act
        status, output = self.run_gate(
            [
                "--env-template",
                str(missing),
                "--compose",
                str(compose),
                "--dockerfile",
                str(dockerfile),
            ]
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn(f"COMPOSE: missing environment template: {missing}", output)

    def test_diagnostics_are_sorted_and_unique(self) -> None:
        # Arrange
        compose = stack(
            web=service(ports=["9443:9443"], healthcheck=OMIT),
            app=service(ports=["8443:8443"], healthcheck=OMIT),
        )

        # Act
        findings = diagnostics(compose)

        # Assert
        self.assertEqual(sorted(set(findings)), findings)
        self.assertEqual(4, len(findings))


class RepositoryComposeTests(QualityGateTestCase):
    def test_the_committed_stack_satisfies_the_policy(self) -> None:
        # Arrange
        deploy = REPOSITORY_ROOT / "deploy"
        composes = sorted(str(path) for path in deploy.glob("**/compose*.y*ml"))
        dockerfiles = sorted(str(path) for path in deploy.glob("**/Dockerfile*"))
        arguments = ["--env-template", str(REPOSITORY_ROOT / ".env.example")]
        for path in composes:
            arguments.extend(["--compose", path])
        for path in dockerfiles:
            arguments.extend(["--dockerfile", path])
        captured = io.StringIO()

        # Act
        with contextlib.redirect_stderr(captured):
            status = compose_policy_gate.main(arguments)

        # Assert
        self.assertEqual(0, status, captured.getvalue())
        self.assertEqual("", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
