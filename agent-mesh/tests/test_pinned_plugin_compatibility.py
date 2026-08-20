"""Whether Agent Mesh 1.28.7 and the two pinned Event Mesh plugins work together.

No upstream artifact attests the combination. ``sam-event-mesh-gateway`` 1.1.0 requires
only ``jsonschema``, ``pydantic`` and ``solace-pubsubplus``; ``sam-event-mesh-tool`` 0.1.1
declares no dependencies at all. Neither names ``solace-agent-mesh``, so nothing in the
metadata would notice if the plugins were built against a different release, and a
resolver cannot fail on a constraint nobody wrote.

The probes therefore reach past resolution to the symbols the plugins actually import from
the runtime. ``import solace_agent_mesh`` on its own proves nothing: its ``__init__.py`` is
empty. The gateway is loaded through the entry point Agent Mesh discovers it by; the tool
declares no entry point and is wired by module path in agent configuration, so it is
imported the same way the runtime would import it.

This is the black-box compatibility class in ``docs/TESTING.md``, run against the exact
pinned wheels. It answers the open question in ``docs/adr/README.md``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import unittest
from typing import Final

import pytest

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]

AGENT_MESH_VERSION: Final = "1.28.7"
GATEWAY_VERSION: Final = "1.1.0"
TOOL_VERSION: Final = "0.1.1"

PLUGIN_GROUP: Final = "solace_agent_mesh.plugins"
GATEWAY_ENTRY_POINT: Final = "sam_event_mesh_gateway"

# The runtime symbols sam_event_mesh_tool.tools imports. If a later Agent Mesh release
# moves or renames any of them, the tool breaks at load time rather than at install time,
# which is exactly the failure no version constraint here can catch.
TOOL_RUNTIME_SYMBOLS: Final = (
    ("solace_agent_mesh.agent.tools.dynamic_tool", "DynamicTool"),
    ("solace_agent_mesh.agent.sac.component", "SamAgentComponent"),
    ("solace_agent_mesh.agent.tools.tool_config_types", "AnyToolConfig"),
    ("google.adk.tools", "ToolContext"),
    ("solace_ai_connector.common.message", "Message"),
)

# The runtime base classes sam_event_mesh_gateway.app and .component subclass.
GATEWAY_RUNTIME_SYMBOLS: Final = (
    ("solace_agent_mesh.gateway.base.app", "BaseGatewayApp"),
    ("solace_agent_mesh.gateway.base.component", "BaseGatewayComponent"),
)


def _attribute(module_name: str, attribute: str) -> object:
    """Return one named attribute of an importable module."""
    return getattr(importlib.import_module(module_name), attribute)


class PinnedVersionTests(unittest.TestCase):
    def test_the_three_pinned_distributions_are_installed_together(self) -> None:
        # Arrange
        expected = (AGENT_MESH_VERSION, GATEWAY_VERSION, TOOL_VERSION)

        # Act
        installed = tuple(
            importlib.metadata.version(name)
            for name in ("solace-agent-mesh", "sam-event-mesh-gateway", "sam-event-mesh-tool")
        )

        # Assert
        self.assertEqual(expected, installed)


class GatewayPluginTests(unittest.TestCase):
    def test_the_gateway_is_discoverable_through_the_agent_mesh_plugin_group(self) -> None:
        # Arrange
        expected = GATEWAY_ENTRY_POINT

        # Act
        discovered = {entry.name for entry in importlib.metadata.entry_points(group=PLUGIN_GROUP)}

        # Assert
        self.assertIn(expected, discovered)

    def test_loading_the_gateway_entry_point_imports_it_against_the_runtime(self) -> None:
        # Arrange
        entry = next(
            item
            for item in importlib.metadata.entry_points(group=PLUGIN_GROUP)
            if item.name == GATEWAY_ENTRY_POINT
        )

        # Act
        info = entry.load()

        # Assert
        self.assertIn("class_name", info)

    def test_every_runtime_base_class_the_gateway_subclasses_is_present(self) -> None:
        # Arrange
        expected = len(GATEWAY_RUNTIME_SYMBOLS)

        # Act
        resolved = tuple(
            _attribute(module, attribute) for module, attribute in GATEWAY_RUNTIME_SYMBOLS
        )

        # Assert
        self.assertEqual(expected, sum(1 for item in resolved if isinstance(item, type)))


class ToolPluginTests(unittest.TestCase):
    def test_the_tool_imports_by_module_path_because_it_declares_no_entry_point(self) -> None:
        # Arrange
        declared = {entry.name for entry in importlib.metadata.entry_points(group=PLUGIN_GROUP)}

        # Act
        module = importlib.import_module("sam_event_mesh_tool.tools")

        # Assert
        self.assertNotIn("sam_event_mesh_tool", declared, module.__name__)

    def test_every_runtime_symbol_the_tool_imports_is_present_and_callable(self) -> None:
        # Arrange
        expected = len(TOOL_RUNTIME_SYMBOLS)

        # Act
        resolved = tuple(
            _attribute(module, attribute) for module, attribute in TOOL_RUNTIME_SYMBOLS
        )

        # Assert
        self.assertEqual(expected, sum(1 for item in resolved if callable(item)))


if __name__ == "__main__":
    unittest.main()
