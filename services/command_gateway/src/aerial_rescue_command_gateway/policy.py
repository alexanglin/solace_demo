"""The deterministic policy that answers one command-gateway request.

``docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md`` closes the
operation set and ``docs/adr/0041-deny-by-default-command-authority-table.md`` closes the
command-type set. This module joins them: it resolves both by exact spelling and reports
what it found. It never records, never consumes an approval, and has no branch that can
publish a command.

Both refusals are ordinary answers rather than dropped messages. A requestor waiting on a
reply would otherwise learn nothing until its timeout, and the audit trail would carry no
record of what was asked.

This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_contracts.rpc import GatewayRequest, GatewayResponse, Outcome
from aerial_rescue_domain.authority import Authority, AuthorityError, authority_for, command_type
from aerial_rescue_domain.operations import OperationError, actuates, operation

AUTHORITY_NAMES: Final[Mapping[Authority, str]] = {
    Authority.GATEWAY_POLICY: "gateway-policy",
    Authority.OPERATOR_APPROVAL: "operator-approval",
}
"""The wire name of each authority, total over them; a test asserts it.

The domain enum's own values are prose, because they exist to be read in a refusal message.
An answer crosses a schema boundary, so it needs a kind-shaped name instead, and the mapping
is written out rather than derived from the member names so that renaming a member is a
visible change to the wire rather than a silent one.
"""


class PolicyRefusal(Enum):
    """Why the command gateway did not answer a request; the value is the wire name."""

    UNKNOWN_OPERATION = "unknown-operation"
    UNKNOWN_COMMAND_TYPE = "unknown-command-type"


def _refused(
    request: GatewayRequest,
    request_id: str,
    refusal: PolicyRefusal,
) -> GatewayResponse:
    """Return the refusal for a request, echoing exactly what was asked."""
    return GatewayResponse(
        mission_id=request.mission_id,
        request_id=request_id,
        operation=request.operation,
        command_type=request.command_type,
        outcome=Outcome.REFUSED,
        actuated=False,
        refusal=refusal.value,
    )


def answer(request: GatewayRequest, request_id: str) -> GatewayResponse:
    """Return the command gateway's answer to one validated request.

    Args:
        request: The validated request body.
        request_id: The identifier of the request being answered, taken from the
            requestor's correlation metadata after it has been validated.

    Returns:
        The answer, whose ``actuated`` is read from the operation table rather than
        asserted, so a future actuating operation cannot report otherwise by omission.
    """
    try:
        requested = operation(request.operation)
    except OperationError:
        return _refused(request, request_id, PolicyRefusal.UNKNOWN_OPERATION)
    try:
        commanded = command_type(request.command_type)
    except AuthorityError:
        return _refused(request, request_id, PolicyRefusal.UNKNOWN_COMMAND_TYPE)
    return GatewayResponse(
        mission_id=request.mission_id,
        request_id=request_id,
        operation=request.operation,
        command_type=request.command_type,
        outcome=Outcome.ANSWERED,
        actuated=actuates(requested),
        authority=AUTHORITY_NAMES[authority_for(commanded)],
    )
