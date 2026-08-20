"""The deny-by-default command-authority table that closes the ``commandType`` set.

The two command types and their authorities are the decision in
``docs/adr/0041-deny-by-default-command-authority-table.md``. Lookup is by exact spelling
and anything else is refused, which is catalogue case B23; a rescue escalation is authorized
only by an approval in the ``EXECUTED`` state, which only the protocol's consumption
produces, so an approved-but-unconsumed record, any other state, or a claim in model output
authorizes nothing (B25). The topic grammar in ``aerial_rescue_contracts`` stays shape-only.
This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError
from aerial_rescue_domain.approvals import ApprovalState


class CommandType(Enum):
    """The closed set of executable command types; every value is a topic kind level."""

    ASSIGN_SECTOR = "assign-sector"
    ESCALATE_RESCUE = "escalate-rescue"


class Authority(Enum):
    """Who decides that a command of a type may be published."""

    GATEWAY_POLICY = "the gateway's deterministic policy decides, with no operator approval"
    OPERATOR_APPROVAL = "requires a consumed operator approval"


class AuthorityRefusal(Enum):
    """Why a command is not authorized."""

    UNKNOWN_COMMAND_TYPE = "command type is absent from the command-authority table"
    APPROVAL_REQUIRED = "command type requires a consumed operator approval"


class AuthorityError(DomainError):
    """A command the table refuses, carrying the refusal as structured data."""


_AUTHORITY: Final[Mapping[CommandType, Authority]] = {
    CommandType.ASSIGN_SECTOR: Authority.GATEWAY_POLICY,
    CommandType.ESCALATE_RESCUE: Authority.OPERATOR_APPROVAL,
}
"""Total over the command types; a test asserts it."""

_BY_KIND: Final[Mapping[str, CommandType]] = {member.value: member for member in CommandType}


def command_type(text: object) -> CommandType:
    """Return the command type spelled exactly by ``text``.

    Args:
        text: The kind level as it arrived.

    Returns:
        The matching member.

    Raises:
        AuthorityError: With ``UNKNOWN_COMMAND_TYPE`` for any other value, text or not.
    """
    member = _BY_KIND.get(text) if isinstance(text, str) else None
    if member is None:
        raise AuthorityError(AuthorityRefusal.UNKNOWN_COMMAND_TYPE, text)
    return member


def authority_for(command: CommandType) -> Authority:
    """Return who decides that a command of this type may be published."""
    return _AUTHORITY[command]


def authorize(text: object, approval: ApprovalState | None) -> CommandType:
    """Return the command type ``text`` names if it may be published given ``approval``.

    Args:
        text: The kind level as it arrived.
        approval: The state of the approval record presented, or ``None`` when there is none.

    Returns:
        The command type, when its authority is satisfied.

    Raises:
        AuthorityError: With ``UNKNOWN_COMMAND_TYPE`` for a spelling outside the table, or
            ``APPROVAL_REQUIRED`` when the type needs a consumed approval and the record
            presented is not in the ``EXECUTED`` state.
    """
    command = command_type(text)
    if (
        authority_for(command) is Authority.OPERATOR_APPROVAL
        and approval is not ApprovalState.EXECUTED
    ):
        raise AuthorityError(AuthorityRefusal.APPROVAL_REQUIRED, text)
    return command
