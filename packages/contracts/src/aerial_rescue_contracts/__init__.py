"""Event envelope, schemas, canonical serialization, and gate contracts."""

from typing import Final

TOPIC_NAMESPACE_ROOT: Final = "aerial-rescue"
"""Root of the application event namespace, kept distinct from Agent Mesh A2A."""

CONTRACT_MAJOR_VERSION: Final = 1
"""Breaking schema changes require a new major topic and schema version."""


def namespace_prefix() -> str:
    """Return the versioned application event namespace prefix.

    Every application topic begins with this prefix, which is what keeps the domain
    contract versioned independently of the Agent Mesh release
    (docs/adr/0014-application-events-separate-from-a2a.md).
    """
    return TOPIC_NAMESPACE_ROOT + "/v" + str(CONTRACT_MAJOR_VERSION)
