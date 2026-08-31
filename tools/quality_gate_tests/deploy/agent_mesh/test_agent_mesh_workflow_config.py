"""What the committed Mission Response workflow may invoke, and what its nodes must produce.

The offline validator in ``agent-mesh/tools/`` proves each file's *shape* against the pinned
wheel's own models. It deliberately does not compare one committed file with another, so the
agreements that make the workflow actually run -- a node's target existing, that target being
instructed to save an artifact, and the output mapping naming members the target's schema
requires -- have no home there. They live here, on the side of the runtime split that can read
every committed file at once (``docs/adr/0029``).

Every assertion below corresponds to an observed failure. The workflow shipped for one phase
mapping ``assess_sectors.output.result`` when no node ever produced a ``result`` member; its
node targeted an agent whose prompt forbids artifacts, so the node failed with
``Artifact ... not found (no versions available)`` on every run; and a chat-mode submission
delivers its message as ``text`` regardless of the declared input schema, so a workflow reading
only a structured member resolved null and died constructing a text part
(``docs/adr/0218-make-a-workflow-node-s-agent-save-its-own-output-artifact.md``).
"""

from __future__ import annotations

import re
import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

CONFIG_ROOT = REPOSITORY_ROOT / "agent-mesh" / "configs"
WORKFLOW_CONFIG = CONFIG_ROOT / "mission-response-workflow.yaml"
GATEWAY_CONFIG = CONFIG_ROOT / "event-mesh-gateway.yaml"
GATEWAY_AGENT = "MissionCoordinator"
"""The Event Mesh Gateway's target, whose prompt forbids the artifact a node requires."""

MAPPING_REFERENCE = re.compile(r"^\{\{\s*(\w+)\.output\.(\w+)\s*\}\}$")
NODE_OUTPUT_REFERENCE = re.compile(r"\{\{\s*(\w+)\.output\.(\w+)\s*\}\}")
"""The same reference anywhere it appears, rather than as a whole mapping value."""
INPUT_REFERENCE = re.compile(r"\{\{\s*workflow\.input\.(\w+)\s*\}\}")
FENCE_PHRASES = ("«««save_artifact:", "«result:artifact=")
"""The literal sequences a workflow-node agent must be shown, not paraphrased."""
FORBIDDEN_PHRASES = ("no artifact", "no tool call")
"""The coordinator's wording, which makes a node fail if copied onto a node agent."""


def _app_config(basename: str) -> dict[str, object]:
    """Return the single ``app_config`` of one committed configuration file."""
    document = yaml.safe_load((CONFIG_ROOT / basename).read_text(encoding="utf-8"))
    return cast(dict[str, object], document["apps"][0]["app_config"])


def _agents() -> dict[str, dict[str, object]]:
    """Return every committed agent's ``app_config``, keyed by its agent name."""
    agents: dict[str, dict[str, object]] = {}
    for path in sorted(CONFIG_ROOT.glob("*.yaml")):
        config = _app_config(path.name)
        name = config.get("agent_name")
        if isinstance(name, str):
            agents[name] = config
    return agents


def _workflow() -> dict[str, object]:
    """Return the committed workflow definition block."""
    return cast(dict[str, object], _app_config(WORKFLOW_CONFIG.name)["workflow"])


def _nodes() -> dict[str, dict[str, object]]:
    """Return the workflow's agent-invoking nodes, keyed by node identifier."""
    nodes = cast(list[dict[str, object]], _workflow()["nodes"])
    return {str(node["id"]): node for node in nodes if node.get("type") == "agent"}


def _required_members(node: dict[str, object], agent: dict[str, object]) -> frozenset[str]:
    """Return the members a node's answer must carry, its override winning over the card."""
    override = node.get("output_schema_override")
    schema = override if isinstance(override, dict) else agent.get("output_schema", {})
    required = cast(dict[str, object], schema).get("required", ())
    return frozenset(str(member) for member in cast(list[object], required))


class WorkflowTargetTests(QualityGateTestCase):
    def test_every_node_targets_an_agent_the_configuration_declares(self) -> None:
        # Arrange
        declared = set(_agents())

        # Act
        targets = {str(node["agent_name"]) for node in _nodes().values()}

        # Assert
        self.assertEqual(set(), targets - declared)

    def test_no_node_targets_the_event_mesh_gateway_s_agent(self) -> None:
        # Arrange
        forbidden = GATEWAY_AGENT

        # Act
        targets = {str(node["agent_name"]) for node in _nodes().values()}

        # Assert
        self.assertNotIn(forbidden, targets)

    def test_the_workflow_allows_exactly_the_agents_its_nodes_name(self) -> None:
        # Arrange
        communication = cast(
            dict[str, object], _app_config(WORKFLOW_CONFIG.name)["inter_agent_communication"]
        )

        # Act
        allowed = set(cast(list[str], communication["allow_list"]))

        # Assert
        self.assertEqual({str(node["agent_name"]) for node in _nodes().values()}, allowed)

    def test_the_gateway_still_invokes_its_own_agent(self) -> None:
        # Arrange
        gateway = _app_config(GATEWAY_CONFIG.name)

        # Act
        handlers = cast(list[dict[str, object]], gateway["event_handlers"])

        # Assert
        self.assertEqual(GATEWAY_AGENT, handlers[0]["target_agent_name"])


class WorkflowOutputTests(QualityGateTestCase):
    def test_the_output_mapping_names_only_members_a_node_must_produce(self) -> None:
        # Arrange
        agents, nodes = _agents(), _nodes()
        mapping = cast(dict[str, object], _workflow()["outputMapping"])

        # Act
        matched = (MAPPING_REFERENCE.match(str(value)) for value in mapping.values())
        missing = {
            f"{found[1]}.{found[2]}"
            for found in matched
            if found is not None
            and found[2]
            not in _required_members(nodes[found[1]], agents[str(nodes[found[1]]["agent_name"])])
        }

        # Assert
        self.assertEqual(set(), missing)

    def test_no_member_a_node_produces_is_silently_discarded(self) -> None:
        """A produced member nothing reads is work thrown away, and worse than that.

        ``SectorPlanner`` returns both a prose ``assessment`` and the ranked ``sectors``
        that are the actual work product. The fusion node was given only the prose and was
        still required to answer with ``contributingSectors``, so it had no choice but to
        invent identifiers: an observed run returned ``['Ridge area', 'Wooded valley']``,
        naming sectors that exist nowhere. Passing the ranked array instead produced
        ``['S1', 'S2']``.

        So this is not tidiness. A member a downstream node is asked to cite but never
        shown is an invitation to fabricate, which is the one failure class a
        search-and-rescue demonstration must not have.
        """
        # Arrange
        agents, nodes = _agents(), _nodes()
        definition = _workflow()
        read_from = f"{definition['nodes']}{definition['outputMapping']}"
        consumed = set(NODE_OUTPUT_REFERENCE.findall(read_from))

        # Act
        discarded = {
            f"{node_id}.{member}"
            for node_id, node in nodes.items()
            for member in _required_members(node, agents[str(node["agent_name"])])
            if (node_id, member) not in consumed
        }

        # Assert
        self.assertEqual(set(), discarded)

    def test_the_workflow_reads_the_input_member_chat_mode_actually_sends(self) -> None:
        # Arrange
        definition = _workflow()

        # Act
        references = set(INPUT_REFERENCE.findall(str(definition["nodes"])))

        # Assert
        self.assertIn("text", references)


class NodeAgentInstructionTests(QualityGateTestCase):
    def test_every_node_agent_is_shown_the_literal_fence_and_result_sequences(self) -> None:
        # Arrange
        agents, nodes = _agents(), _nodes()

        # Act
        lacking = {
            str(node["agent_name"]): [
                phrase
                for phrase in FENCE_PHRASES
                if phrase not in str(agents[str(node["agent_name"])]["instruction"])
            ]
            for node in nodes.values()
        }

        # Assert
        self.assertEqual({name: [] for name in lacking}, lacking)

    def test_no_node_agent_carries_the_wording_that_suppresses_the_artifact(self) -> None:
        # Arrange
        agents, nodes = _agents(), _nodes()

        # Act
        carrying = {
            str(node["agent_name"]): [
                phrase
                for phrase in FORBIDDEN_PHRASES
                if phrase in str(agents[str(node["agent_name"])]["instruction"])
            ]
            for node in nodes.values()
        }

        # Assert
        self.assertEqual({name: [] for name in carrying}, carrying)


if __name__ == "__main__":
    unittest.main()
