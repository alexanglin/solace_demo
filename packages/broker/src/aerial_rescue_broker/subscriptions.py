"""The wildcard subscription strings, which only this package may build.

``docs/CONTRACTS.md`` reserves wildcard construction to the broker adapter: the topic
grammar in ``aerial_rescue_contracts`` refuses a ``*`` or a ``>`` outright, so a published
topic cannot carry one and a subscription cannot be produced there. This module is the
other half of that split.

Each string here has two jobs. It is the subscription a consumer adds, and it is the topic
exception one ACL profile carries under
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``. A pattern
that reaches one level too far therefore does not merely deliver an unwanted message; it
hands a second family's authority to whoever holds the first. Every pattern uses only the
single-level ``*``, and never the multi-level ``>``, for that reason: ``>`` matches the rest
of the topic and cannot be bounded by a later literal level.

The A2A namespace is the one exception. ADR-0014 leaves its shape to Agent Mesh rather than
to the topic grammar, so it is subscribed with ``>``, and the value is injected with no
default because ``NAMESPACE`` is still unset in ``.env.example``. A namespace that would
swallow the application namespace is refused: a ``NAMESPACE`` of ``aerial-rescue`` renders
``aerial-rescue/>``, which reaches every application topic including the drone command
families, and would grant the three Agent Mesh roles the authority the matrix exists to
deny them.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from aerial_rescue_contracts import TOPIC_NAMESPACE_ROOT, namespace_prefix
from aerial_rescue_contracts.topics import (
    MAX_TOPIC_BYTES,
    RESERVED_REPLY_MISSION,
    WILDCARD_CHARACTERS,
    Family,
)

LEVEL_SEPARATOR: Final = "/"
SINGLE_LEVEL_WILDCARD: Final = "*"
MULTI_LEVEL_WILDCARD: Final = ">"


class SubscriptionRefusal(Enum):
    """Why a value cannot become a subscription string."""

    UNSUPPORTED_TYPE = "namespace is not a string"
    WILDCARD = "namespace carries a subscription wildcard"
    EMPTY_LEVEL = "namespace is empty or carries an empty level"
    NAMESPACE_COLLISION = "namespace would swallow the application event namespace"
    LENGTH = "subscription longer than the broker bound"


class SubscriptionError(ValueError):
    """A value this module refuses, carrying the refusal as structured data."""

    refusal: SubscriptionRefusal
    value: object

    def __init__(self, refusal: SubscriptionRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


def _wildcarded(level: str) -> str:
    """Return the single-level wildcard for a template placeholder, else the level itself."""
    placeholder = level.startswith("{") and level.endswith("}")
    return SINGLE_LEVEL_WILDCARD if placeholder else level


def subscription_for(family: Family) -> str:
    """Return the subscription covering every topic in ``family`` and no other family's.

    Args:
        family: The application topic family to subscribe.

    Returns:
        The subscription text, built the way ``format_topic`` builds a topic: the versioned
        namespace prefix, the mission identifier level, then the family template with every
        variable level replaced by a single-level wildcard.
    """
    levels = (
        namespace_prefix(),
        SINGLE_LEVEL_WILDCARD,
        *(_wildcarded(level) for level in family.levels),
    )
    return LEVEL_SEPARATOR.join(levels)


def reply_subscription() -> str:
    """Return the subscription covering the command-gateway reply channel and nothing else.

    The second exception outside the family model, and for the same kind of reason as the
    first: Solace AI Connector binds a requestor's temporary reply queue to both its reply
    topic and that topic followed by ``>``, and neither the requestor identifier nor the
    extra subscription is configurable. A ``*`` at the last level would cover the first and
    not the second, so the bind would be denied
    (``docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md``).

    The levels beneath a requestor identifier are unreachable by the topic grammar, so the
    ``>`` grants authority over topics no producer can publish. What it does not reach is a
    mission: the reserved identifier sits where a mission identifier would, and no event
    may claim it.
    """
    return LEVEL_SEPARATOR.join(
        (
            namespace_prefix(),
            RESERVED_REPLY_MISSION,
            *Family.GATEWAY_RESPONSE.levels[:-1],
            MULTI_LEVEL_WILDCARD,
        )
    )


def a2a_subscription(namespace: object) -> str:
    """Return the subscription covering the Agent Mesh A2A namespace and nothing else.

    Args:
        namespace: The configured A2A namespace, injected with no default because
            ``.env.example`` leaves ``NAMESPACE`` unset until the first Agent Mesh
            configuration fixes it.

    Returns:
        The namespace followed by the multi-level wildcard.

    Raises:
        SubscriptionError: With ``UNSUPPORTED_TYPE`` for a value that is not text,
            ``WILDCARD`` for a value carrying ``*`` or ``>``, ``EMPTY_LEVEL`` for an empty
            value or one with an empty level, ``NAMESPACE_COLLISION`` for a value whose
            first level is the application namespace root, and ``LENGTH`` when the rendered
            subscription exceeds the broker's topic bound.
    """
    if not isinstance(namespace, str):
        raise SubscriptionError(SubscriptionRefusal.UNSUPPORTED_TYPE, namespace)
    if WILDCARD_CHARACTERS & set(namespace):
        raise SubscriptionError(SubscriptionRefusal.WILDCARD, namespace)
    levels = namespace.split(LEVEL_SEPARATOR)
    if not all(levels):
        raise SubscriptionError(SubscriptionRefusal.EMPTY_LEVEL, namespace)
    if levels[0] == TOPIC_NAMESPACE_ROOT:
        raise SubscriptionError(SubscriptionRefusal.NAMESPACE_COLLISION, namespace)
    text = LEVEL_SEPARATOR.join((namespace, MULTI_LEVEL_WILDCARD))
    if len(text.encode()) > MAX_TOPIC_BYTES:
        raise SubscriptionError(SubscriptionRefusal.LENGTH, namespace)
    return text
