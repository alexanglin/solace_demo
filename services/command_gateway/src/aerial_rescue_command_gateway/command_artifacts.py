"""Construct exact command-authorization effects from already trusted values.

This module performs no I/O.  The caller has already validated broker input and decided
whether the command is authorized.  It creates the one audit fact every decision owes and,
only for an authorized decision, the command outbox row and initial progress identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    binding_for,
    check_topic_binding,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic, parse_topic
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.command_progress import CommandIdentity
from aerial_rescue_store.outbox import StagedCommand

from aerial_rescue_command_gateway.ingress import (
    AssignSectorAction,
    OperatorCommandIngress,
)

PRODUCER: Final = "command-gateway"
AUDIT_RECORD_TYPE: Final = "command-authorization"
_EMPTY_HEADERS: Final = canonical.canonical_bytes({})


class ArtifactRefusal(Enum):
    """Why trusted values cannot form the closed output profile."""

    SEQUENCE = "producer sequence is outside the CloudEvents profile"
    REFUSAL_REASON = "a refused authorization requires a closed reason"
    APPROVAL_IDENTITY = "an escalation command requires the consumed approval identity"


class ArtifactError(ValueError):
    """Trusted output values cannot form the closed event profile."""

    def __init__(self, refusal: ArtifactRefusal) -> None:
        """Expose the closed construction refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class AuthorizationStamp:
    """Every output identity and producer fact minted outside broker input."""

    producer_id: str
    command_event_id: str
    audit_record_id: str
    audit_event_id: str
    occurred_at: str
    command_sequence: int
    audit_sequence: int
    traceparent: str
    tracestate: str | None = None


@dataclass(frozen=True)
class AuthorizationArtifacts:
    """The complete durable effect set for one authorization result."""

    audit_record: AuditRecord
    audit_event: StagedApplicationEvent
    command: StagedCommand | None
    progress: CommandIdentity | None
    result: bytes


@dataclass(frozen=True)
class _EventSpec:
    """The output values which vary between command and audit events."""

    topic: Topic
    event_id: str
    sequence: int
    payload: dict[str, object]


def _event(
    ingress: OperatorCommandIngress,
    stamp: AuthorizationStamp,
    spec: _EventSpec,
) -> bytes:
    """Build canonical bytes and re-enter the envelope/topic trust boundary."""
    sequence = sequence_text(spec.sequence)
    if sequence is None:
        raise ArtifactError(ArtifactRefusal.SEQUENCE)
    topic = parse_topic(format_topic(spec.topic))
    kind = event_type(topic)
    document: dict[str, object] = {
        "specversion": "1.0",
        "id": spec.event_id,
        "source": f"urn:aerial-rescue:command-gateway:{stamp.producer_id}",
        "type": kind,
        "subject": ingress.payload.mission_id,
        "time": stamp.occurred_at,
        "datacontenttype": "application/json",
        "dataschema": binding_for(kind).dataschema,
        "data": spec.payload,
        "sequence": sequence,
        "correlationid": ingress.envelope.correlation_id,
        "causationid": ingress.envelope.id,
        "traceparent": stamp.traceparent,
    }
    if stamp.tracestate is not None:
        document["tracestate"] = stamp.tracestate
    envelope = parse_envelope(document)
    check_topic_binding(envelope, topic)
    return canonical.canonical_bytes(document)


def _audit_payload(
    ingress: OperatorCommandIngress,
    stamp: AuthorizationStamp,
    authorized: bool,
    approval_id: str | None,
    reason: str | None,
) -> dict[str, object]:
    """Return one closed audit branch without free-form diagnostic text."""
    payload: dict[str, object] = {
        "auditVersion": 1,
        "missionId": ingress.payload.mission_id,
        "recordId": stamp.audit_record_id,
        "recordType": AUDIT_RECORD_TYPE,
        "commandId": ingress.payload.command_id,
        "operatorId": ingress.payload.operator_id,
        "action": ingress.payload.action.model_dump(),
        "outcome": "authorized" if authorized else "refused",
    }
    if authorized and approval_id is not None:
        payload["approvalId"] = approval_id
    if not authorized:
        if reason is None:
            raise ArtifactError(ArtifactRefusal.REFUSAL_REASON)
        payload["reason"] = reason
    return payload


def _command_payload(
    ingress: OperatorCommandIngress,
    approval_id: str | None,
) -> dict[str, object]:
    """Translate an operator action into the drone command's closed payload."""
    action = ingress.payload.action
    common: dict[str, object] = {
        "missionId": ingress.payload.mission_id,
        "droneId": action.drone_id,
        "commandId": ingress.payload.command_id,
    }
    if isinstance(action, AssignSectorAction):
        common["sectorId"] = action.sector_id
        return common
    if approval_id is None:
        raise ArtifactError(ArtifactRefusal.APPROVAL_IDENTITY)
    common.update(
        {
            "approvalId": approval_id,
            "proposalId": action.proposal_id,
            "proposalDigest": action.proposal_digest,
            "proposalVersion": action.proposal_version,
            "evidenceDecisionId": action.evidence_decision_id,
            "evidenceDecisionDigest": action.evidence_decision_digest,
            "evidenceDecisionVersion": action.evidence_decision_version,
            "latitudeMicrodegrees": action.latitude_microdegrees,
            "longitudeMicrodegrees": action.longitude_microdegrees,
        }
    )
    return common


def build_authorization_artifacts(
    ingress: OperatorCommandIngress,
    stamp: AuthorizationStamp,
    *,
    authorized: bool,
    approval_id: str | None,
    reason: str | None,
) -> AuthorizationArtifacts:
    """Return exact audit and optional command effects for one closed decision."""
    audit_topic = Topic(
        Family.AUDIT,
        ingress.payload.mission_id,
        {"recordType": AUDIT_RECORD_TYPE},
    )
    audit_bytes = _event(
        ingress,
        stamp,
        _EventSpec(
            audit_topic,
            stamp.audit_event_id,
            stamp.audit_sequence,
            _audit_payload(ingress, stamp, authorized, approval_id, reason),
        ),
    )
    audit_event = StagedApplicationEvent(
        producer=PRODUCER,
        event_id=stamp.audit_event_id,
        family="audit",
        topic=format_topic(audit_topic),
        headers=_EMPTY_HEADERS,
        payload=audit_bytes,
        traceparent=stamp.traceparent,
        tracestate=stamp.tracestate,
        correlation_id=ingress.envelope.correlation_id,
        causation_id=ingress.envelope.id,
        staged_at=stamp.occurred_at,
    )
    audit_record = AuditRecord(
        mission_id=ingress.payload.mission_id,
        kind=parse_envelope(canonical.decode(audit_bytes)).type,
        occurred_at=stamp.occurred_at,
        payload=audit_bytes,
        correlation_id=ingress.envelope.correlation_id,
        causation_id=ingress.envelope.id,
        traceparent=stamp.traceparent,
    )
    result_document: dict[str, object] = {
        "authorization": "authorized" if authorized else "refused",
        "commandId": ingress.payload.command_id,
    }
    if reason is not None:
        result_document["reason"] = reason
    result = canonical.canonical_bytes(result_document)
    if not authorized:
        return AuthorizationArtifacts(audit_record, audit_event, None, None, result)

    action = ingress.payload.action
    command_topic = Topic(
        Family.DRONE_COMMAND,
        ingress.payload.mission_id,
        {"droneId": action.drone_id, "commandType": action.command_type},
    )
    command_bytes = _event(
        ingress,
        stamp,
        _EventSpec(
            command_topic,
            stamp.command_event_id,
            stamp.command_sequence,
            _command_payload(ingress, approval_id),
        ),
    )
    command = StagedCommand(
        command_id=ingress.payload.command_id,
        mission_id=ingress.payload.mission_id,
        drone_id=action.drone_id,
        payload=command_bytes,
        correlation_id=ingress.envelope.correlation_id,
        causation_id=ingress.envelope.id,
        traceparent=stamp.traceparent,
        staged_at=stamp.occurred_at,
    )
    progress = CommandIdentity(
        ingress.payload.command_id,
        ingress.payload.mission_id,
        action.drone_id,
    )
    return AuthorizationArtifacts(audit_record, audit_event, command, progress, result)
