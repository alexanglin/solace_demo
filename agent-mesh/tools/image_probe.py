"""Whether the pinned Agent Mesh wheels work on the interpreter inside the built image.

``agent-mesh/tests/test_pinned_plugin_compatibility.py`` answers this for the verification
environment, which runs CPython 3.13.15 from ``agent-mesh/.venv``. The runtime is a different
interpreter: upstream builds ``solace/solace-agent-mesh:1.28.7`` on CPython 3.13.11, and
``deploy/agent-mesh/Dockerfile`` installs the two plugin wheels into that image's own
``/opt/venv`` by hash. ``docs/ARCHITECTURE.md`` requires the probe to be run inside the built
image before the ``mesh`` profile is called supported, and ``TECH_DEBT.md`` section 6 carries
the gap until it is.

This is a script rather than a test, and it imports nothing beyond the standard library and the
distributions under test, because the image's ``/opt/venv`` carries no pytest and adding one to
a runtime image would widen it for the sake of the thing measuring it. It is run by
``scripts/probes/agent-mesh-image-probe.sh``, which mounts this directory read-only into a
throwaway container.

Every check returns a line of evidence rather than a boolean, so a passing run is a record worth
committing and a failing run names what differed.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from collections.abc import Callable
from typing import Final

EXPECTED_VERSIONS: Final[dict[str, str]] = {
    "solace-agent-mesh": "1.28.7",
    "sam-event-mesh-gateway": "1.1.0",
    "sam-event-mesh-tool": "0.1.1",
    "solace-ai-connector": "3.3.12",
    "solace-pubsubplus": "1.11.0",
}
PLUGIN_GROUP: Final = "solace_agent_mesh.plugins"
GATEWAY_ENTRY_POINT: Final = "sam_event_mesh_gateway"
GATEWAY_CLASS: Final = "EventMeshGatewayApp"
TOOL_MODULE: Final = "sam_event_mesh_tool.tools"
TOOL_CLASS: Final = "EventMeshTool"
RUNTIME_SYMBOLS: Final[tuple[tuple[str, str], ...]] = (
    ("aerial_rescue_runtime_compat.messaging", "install_hardened_messaging"),
    ("aerial_rescue_event_mesh_gateway.app", "AerialRescueEventMeshGatewayApp"),
    (
        "aerial_rescue_event_mesh_gateway.component",
        "AerialRescueEventMeshGatewayComponent",
    ),
    ("solace_agent_mesh.agent.tools.dynamic_tool", "DynamicTool"),
    ("solace_agent_mesh.agent.sac.component", "SamAgentComponent"),
    ("solace_agent_mesh.agent.tools.tool_config_types", "AnyToolConfig"),
    ("google.adk.tools", "ToolContext"),
    ("solace_ai_connector.common.message", "Message"),
    ("solace_agent_mesh.gateway.base.app", "BaseGatewayApp"),
    ("solace_agent_mesh.gateway.base.component", "BaseGatewayComponent"),
)
"""What the two plugins import from the runtime. A pin cannot prove any of it: a release that
moved or renamed one would install cleanly and fail at load time, inside the image."""
DIRECT_OUTPUT_METHODS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "solace.messaging.messaging_service",
        "MessagingService",
        "create_direct_message_publisher_builder",
    ),
    (
        "solace.messaging.builder.direct_message_publisher_builder",
        "DirectMessagePublisherBuilder",
        "on_back_pressure_reject",
    ),
    (
        "solace.messaging.builder.direct_message_publisher_builder",
        "DirectMessagePublisherBuilder",
        "build",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "set_publish_failure_listener",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "set_publisher_readiness_listener",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "start",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "is_ready",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "notify_when_ready",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "publish",
    ),
    (
        "solace.messaging.publisher.direct_message_publisher",
        "DirectMessagePublisher",
        "terminate",
    ),
    (
        "solace.messaging.publisher.persistent_message_publisher",
        "PersistentMessagePublisher",
        "set_message_publish_receipt_listener",
    ),
    ("solace.messaging.resources.topic", "Topic", "of"),
)
PUBLISH_RECEIPT_CLASS: Final = (
    "solace.messaging.publisher.persistent_message_publisher",
    "PublishReceipt",
)
PUBLISH_RECEIPT_PROPERTIES: Final = ("exception", "is_persisted", "user_context")


class ProbeError(RuntimeError):
    """A check whose answer differs from what the pins promise."""


class DirectOutputApiError(ProbeError):
    """The runtime image lacks part of the owned gateway's pinned SDK seam."""

    def __init__(self) -> None:
        """Create the fixed, credential-safe probe diagnostic."""
        super().__init__("direct output SDK surface is absent")


def _interpreter() -> str:
    """Report the interpreter the image actually runs, which upstream chooses."""
    return f"interpreter: CPython {'.'.join(str(part) for part in sys.version_info[:3])}"


def _versions() -> str:
    """Report the attested runtime distributions, refusing any version other than the pin.

    Raises:
        ProbeError: If a distribution is absent or resolves to another version.
    """
    resolved: dict[str, str] = {}
    for name in EXPECTED_VERSIONS:
        try:
            resolved[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as absent:
            message = f"{name} is not installed in the image"
            raise ProbeError(message) from absent
    if resolved != EXPECTED_VERSIONS:
        message = f"expected {EXPECTED_VERSIONS}, found {resolved}"
        raise ProbeError(message)
    return "versions: " + ", ".join(f"{name}=={value}" for name, value in resolved.items())


def _gateway_entry_point() -> str:
    """Load the gateway through the plugin group the runtime discovers it by.

    Raises:
        ProbeError: If the entry point is absent or does not name the gateway app class.
    """
    entries = [
        entry
        for entry in importlib.metadata.entry_points(group=PLUGIN_GROUP)
        if entry.name == GATEWAY_ENTRY_POINT
    ]
    if len(entries) != 1:
        message = f"expected exactly one {GATEWAY_ENTRY_POINT} entry point, found {len(entries)}"
        raise ProbeError(message)
    info = entries[0].load()
    if info.get("class_name") != GATEWAY_CLASS:
        message = f"entry point loaded {info.get('class_name')!r}, expected {GATEWAY_CLASS!r}"
        raise ProbeError(message)
    return f"gateway: {PLUGIN_GROUP}:{GATEWAY_ENTRY_POINT} loads {GATEWAY_CLASS}"


def _tool_module() -> str:
    """Import the tool by module path, which is how it is wired: it declares no entry point.

    Raises:
        ProbeError: If the module or its class is absent.
    """
    try:
        module = importlib.import_module(TOOL_MODULE)
    except ImportError as absent:
        message = f"{TOOL_MODULE} does not import in the image"
        raise ProbeError(message) from absent
    if not hasattr(module, TOOL_CLASS):
        message = f"{TOOL_MODULE} has no {TOOL_CLASS}"
        raise ProbeError(message)
    return f"tool: {TOOL_MODULE}:{TOOL_CLASS} imports by module path"


def _runtime_symbols() -> str:
    """Resolve every runtime symbol the tool depends on, which pins alone do not prove.

    Raises:
        ProbeError: If any symbol the tool imports is absent from the runtime.
    """
    missing = [
        f"{module_name}:{symbol}"
        for module_name, symbol in RUNTIME_SYMBOLS
        if not hasattr(importlib.import_module(module_name), symbol)
    ]
    if missing:
        message = f"runtime symbols absent: {', '.join(missing)}"
        raise ProbeError(message)
    return f"runtime symbols: {len(RUNTIME_SYMBOLS)} resolved"


def _direct_output_api() -> str:
    """Resolve every public SDK member used by the owned Direct output component.

    Raises:
        ProbeError: If the runtime image does not provide the attested SDK surface.
    """
    try:
        methods = tuple(
            getattr(getattr(importlib.import_module(module), class_name), method_name)
            for module, class_name, method_name in DIRECT_OUTPUT_METHODS
        )
        receipt_class = getattr(
            importlib.import_module(PUBLISH_RECEIPT_CLASS[0]),
            PUBLISH_RECEIPT_CLASS[1],
        )
        receipt_properties = tuple(
            getattr(receipt_class, property_name) for property_name in PUBLISH_RECEIPT_PROPERTIES
        )
    except Exception as absent:
        raise DirectOutputApiError from absent
    if not all(callable(method) for method in methods) or not all(
        isinstance(value, property) for value in receipt_properties
    ):
        raise DirectOutputApiError
    return (
        f"direct output API: {len(methods)} methods and "
        f"{len(receipt_properties)} receipt properties resolved"
    )


CHECKS: Final[tuple[Callable[[], str], ...]] = (
    _interpreter,
    _versions,
    _gateway_entry_point,
    _tool_module,
    _runtime_symbols,
    _direct_output_api,
)


def main() -> int:
    """Run every check, printing one redacted evidence line for every outcome.

    Returns:
        0 when every check answered as the pins promise, 1 otherwise.
    """
    failures = 0
    for check in CHECKS:
        try:
            print(f"PASS {check()}")
        except ProbeError as failure:
            print(f"FAIL {check.__name__}: {failure}", file=sys.stderr)
            failures += 1
        except Exception:
            print(f"FAIL {check.__name__}: unexpected probe failure", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
