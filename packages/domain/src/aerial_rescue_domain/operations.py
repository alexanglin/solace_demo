"""The deny-by-default gateway-operation table that closes the ``operation`` kind set.

The one operation and its actuation are the decision in
``docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md``. Lookup
is by exact spelling and anything else is refused, which matters more here than for a
command type: the level this table closes is rendered from an Event Mesh Tool parameter,
and a parameter the model can see is a parameter the model can set, so an invented
spelling is the expected input rather than the exceptional one.

``command-authority`` is read-only. It answers which authority a command type falls under
under ``authority.py``; it records nothing, consumes no approval, and publishes no
command. The topic grammar in ``aerial_rescue_contracts`` stays shape-only. This module is
pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class Operation(Enum):
    """The closed set of command-gateway operations; every value is a topic kind level."""

    COMMAND_AUTHORITY = "command-authority"


class OperationRefusal(Enum):
    """Why a requested operation is not performed."""

    UNKNOWN_OPERATION = "operation is absent from the gateway-operation table"


class OperationError(DomainError):
    """An operation the table refuses, carrying the refusal as structured data."""


_ACTUATES: Final[Mapping[Operation, bool]] = {
    Operation.COMMAND_AUTHORITY: False,
}
"""Whether performing an operation may publish an executable command.

Total over the operations; a test asserts it. Every row is ``False`` today, which is what
makes the Phase 0 claim of a non-actuating response something the table states rather than
something the prose asserts. A row that becomes ``True`` is a change to the safety
boundary in ``docs/adr/0005-deterministic-command-gateway.md`` and needs its own record.
"""

_BY_KIND: Final[Mapping[str, Operation]] = {member.value: member for member in Operation}


def operation(text: object) -> Operation:
    """Return the operation spelled exactly by ``text``.

    Args:
        text: The kind level as it arrived.

    Returns:
        The matching member.

    Raises:
        OperationError: With ``UNKNOWN_OPERATION`` for any other value, text or not.
    """
    member = _BY_KIND.get(text) if isinstance(text, str) else None
    if member is None:
        raise OperationError(OperationRefusal.UNKNOWN_OPERATION, text)
    return member


def actuates(requested: Operation) -> bool:
    """Return whether performing ``requested`` may publish an executable command."""
    return _ACTUATES[requested]
