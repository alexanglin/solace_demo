"""Deterministic command gateway: policy, approval consumption, dispatch.

``docs/adr/0005-deterministic-command-gateway.md`` makes this service the sole component
permitted to publish executable command topics, which is why every module here is pure and
every value that reaches it from a broker message is treated as untrusted input. The clock,
the identifier source, and the producer sequence are supplied by the composition root, so
nothing in this package reads a clock or consumes a random source.

Refusals are :class:`aerial_rescue_domain.DomainError` subclasses, which is the shape that
package documents as existing so this one can audit every denied attempt through a single
handler.
"""

from __future__ import annotations

from typing import Final

from aerial_rescue_contracts import TOPIC_NAMESPACE_ROOT
from aerial_rescue_domain import DomainError
from aerial_rescue_domain.principals import Principal

URN_SCHEME: Final = "urn"
URN_SEPARATOR: Final = ":"
PRODUCER_KIND: Final = "service"
"""The ``producerKind`` level of this service's CloudEvents ``source``."""


class CommandGatewayError(DomainError):
    """A value the command gateway refuses, carrying the refusal as structured data."""


def event_source() -> str:
    """Return the CloudEvents ``source`` this service publishes every record under.

    The producer identifier is the broker authorization role's own name, so the identity in
    an audit record and the identity the broker authenticated cannot drift
    (``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``), and
    the namespace is the one the topic grammar uses, so those two cannot drift either.
    """
    return URN_SEPARATOR.join(
        (URN_SCHEME, TOPIC_NAMESPACE_ROOT, PRODUCER_KIND, Principal.COMMAND_GATEWAY.value)
    )
