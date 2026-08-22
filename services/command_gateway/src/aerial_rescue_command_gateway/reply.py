"""Where an answer may be sent, decided from user properties the requestor supplied.

This is the injection guard named in
``docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md``. The reply
topic and the correlation metadata both arrive from whoever sent the request, and the
component reading them is the only one permitted to publish executable commands
(``docs/adr/0005-deterministic-command-gateway.md``). Obeying an arbitrary reply topic
would aim that component wherever a caller liked, so a topic that is not a gateway-response
topic on the reserved reply identifier is refused rather than trusted.

The metadata is Solace AI Connector's own correlation stack, not this project's contract,
so it is read with the standard JSON decoder rather than the canonical one: its keys are
``request_id`` and ``response_topic``, which the canonical key rule forbids. Exactly one
value is read out of it, and the string itself is echoed back byte for byte -- the
connector pops the last entry and reads ``request_id``, so a re-encoded stack would still
correlate but would no longer be what the requestor sent.

This module is pure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.topics import (
    IDENTIFIER_PATTERN,
    RESERVED_REPLY_MISSION,
    Family,
    TopicError,
    parse_topic,
)

from aerial_rescue_command_gateway import CommandGatewayError

REPLY_TOPIC_KEY: Final = "__solace_ai_connector_broker_request_response_topic__"
REPLY_METADATA_KEY: Final = "__solace_ai_connector_broker_request_reply_metadata__"
"""The two user-property keys Solace AI Connector 3.3.12 sets on every request."""

MAX_REPLY_METADATA_BYTES: Final = 4096
"""Bound on the correlation stack, in UTF-8 bytes; see docs/operating-parameters.md."""

_REQUEST_ID_KEY: Final = "request_id"


class ReplyRefusal(Enum):
    """Why an answer cannot be sent anywhere."""

    NOT_A_MAPPING = "user properties are not a mapping"
    MISSING_TOPIC = "reply topic is absent or is not text"
    TOPIC_FORM = "reply topic is not an application topic"
    TOPIC_FAMILY = "reply topic is not on the gateway-response family"
    TOPIC_MISSION = "reply topic does not sit on the reserved reply identifier"
    MISSING_METADATA = "reply metadata is absent or is not text"
    METADATA_LENGTH = "reply metadata is longer than the bound"
    METADATA_FORM = "reply metadata is not a JSON array"
    METADATA_EMPTY = "reply metadata stack is empty"
    REQUEST_ID = "reply metadata names no identifier as its request"


class ReplyError(CommandGatewayError):
    """A reply target the command gateway refuses, carrying the refusal as data."""


@dataclass(frozen=True)
class ReplyTarget:
    """Where one answer goes, and what must travel back with it."""

    topic: str
    request_id: str
    metadata: str


def _properties(value: object) -> Mapping[object, object]:
    """Return the user properties, refusing anything that is not a mapping."""
    if not isinstance(value, Mapping):
        raise ReplyError(ReplyRefusal.NOT_A_MAPPING, value)
    return value


def _topic(properties: Mapping[object, object]) -> str:
    """Return the reply topic, refusing anything outside the reserved reply channel."""
    text = properties.get(REPLY_TOPIC_KEY)
    if not isinstance(text, str):
        raise ReplyError(ReplyRefusal.MISSING_TOPIC, text)
    try:
        topic = parse_topic(text)
    except TopicError as error:
        raise ReplyError(ReplyRefusal.TOPIC_FORM, text) from error
    if topic.family is not Family.GATEWAY_RESPONSE:
        raise ReplyError(ReplyRefusal.TOPIC_FAMILY, text)
    if topic.mission_id != RESERVED_REPLY_MISSION:
        raise ReplyError(ReplyRefusal.TOPIC_MISSION, text)
    return text


def _stack(properties: Mapping[object, object]) -> tuple[str, list[object]]:
    """Return the metadata string and the decoded stack, refusing a malformed one."""
    text = properties.get(REPLY_METADATA_KEY)
    if not isinstance(text, str):
        raise ReplyError(ReplyRefusal.MISSING_METADATA, text)
    size = len(text.encode())
    if size > MAX_REPLY_METADATA_BYTES:
        raise ReplyError(ReplyRefusal.METADATA_LENGTH, size)
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReplyError(ReplyRefusal.METADATA_FORM, text) from error
    if not isinstance(decoded, list):
        raise ReplyError(ReplyRefusal.METADATA_FORM, text)
    if not decoded:
        raise ReplyError(ReplyRefusal.METADATA_EMPTY, text)
    return text, decoded


def _request_id(stack: list[object]) -> str:
    """Return the identifier the last stack entry names, refusing anything else.

    The connector pops the last entry when it correlates the reply, so the last entry is
    the request being answered.
    """
    entry = stack[-1]
    named = entry.get(_REQUEST_ID_KEY) if isinstance(entry, Mapping) else None
    if not isinstance(named, str) or re.fullmatch(IDENTIFIER_PATTERN, named) is None:
        raise ReplyError(ReplyRefusal.REQUEST_ID, named)
    return named


def reply_target(user_properties: object) -> ReplyTarget:
    """Return where one answer may be sent, from the requestor's own user properties.

    Args:
        user_properties: The message's user properties, exactly as they arrived.

    Returns:
        The reply topic, the request being answered, and the metadata to echo unchanged.

    Raises:
        ReplyError: With a typed refusal for properties that are not a mapping, a reply
            topic that is absent, malformed, on another family, or on a real mission, and
            metadata that is absent, oversized, not a JSON array, empty, or names no
            identifier as its request.
    """
    properties = _properties(user_properties)
    topic = _topic(properties)
    metadata, stack = _stack(properties)
    return ReplyTarget(topic=topic, request_id=_request_id(stack), metadata=metadata)
