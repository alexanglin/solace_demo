"""Strict service-local twins of dashboard and scenario-control wire schemas."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.instant import parse_instant
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)

_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/"
_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    validate_by_alias=True,
    validate_by_name=False,
    serialize_by_alias=True,
)
_ROOT_MODEL_CONFIG = ConfigDict(
    frozen=True,
    strict=True,
    validate_by_alias=True,
    validate_by_name=False,
    serialize_by_alias=True,
)


def _calendar_instant(value: str) -> str:
    """Require an instant to name a real calendar date."""
    parse_instant(value)
    return value


def _strict_literal_one(value: object) -> object:
    """Keep a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "Input should be the integer 1"
        raise ValueError(message)
    return value


type _Identifier = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$",
        max_length=64,
    ),
]
type _AgentName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]{1,64}$")]
type _Kind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=32),
]
type _Instant = Annotated[str, AfterValidator(_calendar_instant)]
type _Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type _NonEmptyString = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
type _Cursor = _NonEmptyString
type _SafePositiveInteger = Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
type _SafeNonNegativeInteger = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
type _Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type _Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
type _Percent = Annotated[int, Field(ge=0, le=100)]
type _Altitude = Annotated[int, Field(ge=-500, le=20_000)]
type _Heading = Annotated[int, Field(ge=0, le=359)]
type _GroundSpeed = Annotated[int, Field(ge=0, le=10_000)]
type _StrictOne = Annotated[Literal[1], BeforeValidator(_strict_literal_one)]
type _MissionLifecycle = Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]
type _Connectivity = Literal["CONNECTED", "DEGRADED", "OFFLINE"]
type _SectorState = Literal["UNASSIGNED", "ASSIGNED", "AT_RISK", "SEARCHED"]
type _AgentAbstentionReason = Literal[
    "timeout", "transport-error", "model-error", "invalid-output", "identity-mismatch"
]


class _WireModel(BaseModel):
    model_config = _MODEL_CONFIG


class _AssignSectorAction(_WireModel):
    command_type: Literal["assign-sector"] = Field(alias="commandType")
    drone_id: _Identifier = Field(alias="droneId")
    sector_id: _Identifier = Field(alias="sectorId")


class _EscalateRescueCommandAction(_WireModel):
    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: _Identifier = Field(alias="droneId")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: _Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: _StrictOne = Field(alias="evidenceDecisionVersion")
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


type _OperatorCommandAction = Annotated[
    _AssignSectorAction | _EscalateRescueCommandAction,
    Field(discriminator="command_type"),
]


class _EscalateRescueAction(_WireModel):
    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: _Identifier = Field(alias="droneId")
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _Bootstrap(_WireModel):
    bootstrap_version: Literal["dashboard-bootstrap/v1"] = Field(alias="bootstrapVersion")
    bearer: _NonEmptyString
    runtime_id: _Identifier = Field(alias="runtimeId")


class _TelemetryReading(_WireModel):
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")
    battery_percent: _Percent = Field(alias="batteryPercent")
    altitude_metres: _Altitude = Field(alias="altitudeMetres")
    heading_degrees: _Heading = Field(alias="headingDegrees")
    ground_speed_centimetres_per_second: _GroundSpeed = Field(
        alias="groundSpeedCentimetresPerSecond"
    )


class _TelemetryData(_TelemetryReading):
    drone_id: _Identifier = Field(alias="droneId")


class _DroneTelemetryEvent(_WireModel):
    kind: Literal["droneTelemetry"]
    event_class: Literal["TELEMETRY"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _TelemetryData


class _ConnectivityData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    connectivity: _Connectivity


class _ConnectivityChangedEvent(_WireModel):
    kind: Literal["connectivityChanged"]
    event_class: Literal["CONNECTIVITY"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _ConnectivityData


class _MissionLifecycleData(_WireModel):
    lifecycle: _MissionLifecycle


class _MissionLifecycleEvent(_WireModel):
    kind: Literal["missionLifecycle"]
    event_class: Literal["MISSION"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _MissionLifecycleData


class _SectorLifecycleData(_WireModel):
    sector_id: _Identifier = Field(alias="sectorId")
    state: _SectorState
    assigned_member_id: _Identifier | None = Field(alias="assignedMemberId")


class _SectorLifecycleEvent(_WireModel):
    kind: Literal["sectorLifecycle"]
    event_class: Literal["MISSION"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _SectorLifecycleData


class _OperatorCommandData(_WireModel):
    operator_command_version: _StrictOne = Field(alias="operatorCommandVersion")
    command_id: _Identifier = Field(alias="commandId")
    operator_id: _Identifier = Field(alias="operatorId")
    action: _OperatorCommandAction


class _OperatorCommandEvent(_WireModel):
    kind: Literal["operatorCommand"]
    event_class: Literal["COMMAND"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _OperatorCommandData


class _OperatorApprovalData(_WireModel):
    operator_approval_version: _StrictOne = Field(alias="operatorApprovalVersion")
    approval_id: _Identifier = Field(alias="approvalId")
    operator_id: _Identifier = Field(alias="operatorId")
    issued_at: _Instant = Field(alias="issuedAt")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: _Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: _StrictOne = Field(alias="evidenceDecisionVersion")
    action: _EscalateRescueAction


class _ApprovedOperatorApprovalData(_OperatorApprovalData):
    decision: Literal["approve"]
    expires_at: _Instant = Field(alias="expiresAt")


class _RejectedOperatorApprovalData(_OperatorApprovalData):
    decision: Literal["reject"]


type _OperatorApprovalDataValue = Annotated[
    _ApprovedOperatorApprovalData | _RejectedOperatorApprovalData,
    Field(discriminator="decision"),
]


class _OperatorApprovalEvent(_WireModel):
    kind: Literal["operatorApproval"]
    event_class: Literal["APPROVAL"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _OperatorApprovalDataValue


class _AgentProposalData(_WireModel):
    canonicalization_version: _StrictOne = Field(alias="canonicalizationVersion")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_type: Literal["candidate-location"] = Field(alias="proposalType")
    agent_name: _AgentName = Field(alias="agentName")
    source_invocation_id: _Identifier = Field(alias="sourceInvocationId")
    source_event_id: _Identifier = Field(alias="sourceEventId")
    source_event_digest: _Digest = Field(alias="sourceEventDigest")
    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: _Identifier = Field(alias="droneId")
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")
    proposal_digest: _Digest = Field(alias="proposalDigest")


class _AgentProposalEvent(_WireModel):
    kind: Literal["agentProposal"]
    event_class: Literal["EVIDENCE"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _AgentProposalData


class _LiveModelContributor(_WireModel):
    evidence_item_id: _Identifier = Field(alias="evidenceItemId")
    source_id: _Identifier = Field(alias="sourceId")
    origin: Literal["live-model"]
    weight: Literal[35]
    provenance_digest: _Digest = Field(alias="provenanceDigest")


class _LiveSensorContributor(_WireModel):
    evidence_item_id: _Identifier = Field(alias="evidenceItemId")
    source_id: _Identifier = Field(alias="sourceId")
    origin: Literal["live-sensor"]
    weight: Literal[40]
    provenance_digest: _Digest = Field(alias="provenanceDigest")


type _EvidenceContributor = Annotated[
    _LiveModelContributor | _LiveSensorContributor,
    Field(discriminator="origin"),
]


class _EvidenceDecisionData(_WireModel):
    canonicalization_version: _StrictOne = Field(alias="canonicalizationVersion")
    evidence_decision_version: _StrictOne = Field(alias="evidenceDecisionVersion")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")


class _ContributingEvidenceDecisionData(_EvidenceDecisionData):
    outcome: Literal["contributing"]
    score_version: _StrictOne = Field(alias="scoreVersion")
    score: _Percent
    band: Literal["none", "weak", "supported", "corroborated"]
    contributors: Annotated[list[_EvidenceContributor], Field(min_length=1, max_length=23)]


class _ManualReviewEvidenceDecisionData(_EvidenceDecisionData):
    outcome: Literal["manual-review"]
    reason: Literal["policy-referral", "conflicting-evidence", "insufficient-live-sources"]


class _AbstainedEvidenceDecisionData(_EvidenceDecisionData):
    outcome: Literal["abstained"]
    reason: Literal[
        "timeout",
        "transport-error",
        "model-error",
        "invalid-output",
        "identity-mismatch",
        "declined",
    ]


class _RejectedEvidenceDecisionData(_EvidenceDecisionData):
    outcome: Literal["rejected"]
    reason: Literal[
        "invalid-output",
        "identity-mismatch",
        "provenance-missing",
        "provenance-mismatch",
        "recorded-origin",
        "human-dismissal",
    ]


type _EvidenceDecisionDataValue = Annotated[
    _ContributingEvidenceDecisionData
    | _ManualReviewEvidenceDecisionData
    | _AbstainedEvidenceDecisionData
    | _RejectedEvidenceDecisionData,
    Field(discriminator="outcome"),
]


class _EvidenceDecisionEvent(_WireModel):
    kind: Literal["evidenceDecision"]
    event_class: Literal["EVIDENCE"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _EvidenceDecisionDataValue


class _SalientObservationData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    observation: _Kind
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")
    detail: _NonEmptyString


class _SalientObservationEvent(_WireModel):
    kind: Literal["salientObservation"]
    event_class: Literal["EVIDENCE"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _SalientObservationData


class _AssignSectorDroneCommandData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    command_id: _Identifier = Field(alias="commandId")
    sector_id: _Identifier = Field(alias="sectorId")


class _EscalateRescueDroneCommandData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    command_id: _Identifier = Field(alias="commandId")
    approval_id: _Identifier = Field(alias="approvalId")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: _Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: _StrictOne = Field(alias="evidenceDecisionVersion")
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _DroneCommandEvent(_WireModel):
    kind: Literal["droneCommand"]
    event_class: Literal["COMMAND"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _AssignSectorDroneCommandData | _EscalateRescueDroneCommandData


class _CommandResultData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    command_id: _Identifier = Field(alias="commandId")
    outcome: Literal["acknowledged", "succeeded", "failed"]


class _CommandResultEvent(_WireModel):
    kind: Literal["commandResult"]
    event_class: Literal["COMMAND"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _CommandResultData


class _GatewayResponseData(_WireModel):
    rpc_version: _StrictOne = Field(alias="rpcVersion")
    request_id: _Identifier = Field(alias="requestId")
    operation: _Kind
    command_type: _Kind = Field(alias="commandType")
    actuated: bool


class _AnsweredGatewayResponseData(_GatewayResponseData):
    outcome: Literal["answered"]
    authority: _Kind


class _RefusedGatewayResponseData(_GatewayResponseData):
    outcome: Literal["refused"]
    refusal: _Kind


type _GatewayResponseDataValue = Annotated[
    _AnsweredGatewayResponseData | _RefusedGatewayResponseData,
    Field(discriminator="outcome"),
]


class _GatewayResponseEvent(_WireModel):
    kind: Literal["gatewayResponse"]
    event_class: Literal["AUDIT"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _GatewayResponseDataValue


class _ProposalNormalizationAuditData(_WireModel):
    audit_version: _StrictOne = Field(alias="auditVersion")
    record_id: _Identifier = Field(alias="recordId")
    record_type: Literal["proposal-normalization"] = Field(alias="recordType")
    agent_name: _AgentName = Field(alias="agentName")
    invocation_id: _Identifier = Field(alias="invocationId")
    correlation_id: _Identifier = Field(alias="correlationId")


class _NormalizedProposalAuditData(_ProposalNormalizationAuditData):
    outcome: Literal["normalized"]
    source_event_id: _Identifier = Field(alias="sourceEventId")
    source_event_digest: _Digest = Field(alias="sourceEventDigest")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")


class _AbstainedProposalAuditData(_ProposalNormalizationAuditData):
    outcome: Literal["abstained"]
    reason: _AgentAbstentionReason


class _RefusedProposalAuditData(_ProposalNormalizationAuditData):
    outcome: Literal["refused"]
    reason: Literal[
        "schema-invalid",
        "correlation-mismatch",
        "identity-mismatch",
        "unsupported-action",
        "digest-mismatch",
    ]


class _EvidenceDecisionAuditData(_WireModel):
    audit_version: _StrictOne = Field(alias="auditVersion")
    record_id: _Identifier = Field(alias="recordId")
    record_type: Literal["evidence-decision"] = Field(alias="recordType")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: _Digest = Field(alias="evidenceDecisionDigest")


class _ContributingEvidenceAuditData(_EvidenceDecisionAuditData):
    outcome: Literal["contributing"]


class _ManualReviewEvidenceAuditData(_EvidenceDecisionAuditData):
    outcome: Literal["manual-review"]
    reason: Literal["policy-referral", "conflicting-evidence", "insufficient-live-sources"]


class _AbstainedEvidenceAuditData(_EvidenceDecisionAuditData):
    outcome: Literal["abstained"]
    reason: Literal[
        "timeout",
        "transport-error",
        "model-error",
        "invalid-output",
        "identity-mismatch",
        "declined",
    ]


class _RejectedEvidenceAuditData(_EvidenceDecisionAuditData):
    outcome: Literal["rejected"]
    reason: Literal[
        "invalid-output",
        "identity-mismatch",
        "provenance-missing",
        "provenance-mismatch",
        "recorded-origin",
        "human-dismissal",
    ]


class _CommandAuthorizationAuditData(_WireModel):
    audit_version: _StrictOne = Field(alias="auditVersion")
    record_id: _Identifier = Field(alias="recordId")
    record_type: Literal["command-authorization"] = Field(alias="recordType")
    command_id: _Identifier = Field(alias="commandId")
    operator_id: _Identifier = Field(alias="operatorId")


class _AuthorizedAssignSectorAuditData(_CommandAuthorizationAuditData):
    action: _AssignSectorAction
    outcome: Literal["authorized"]


class _AuthorizedEscalateRescueAuditData(_CommandAuthorizationAuditData):
    action: _EscalateRescueCommandAction
    outcome: Literal["authorized"]
    approval_id: _Identifier = Field(alias="approvalId")


type _CommandRefusalReason = Literal[
    "approval-missing",
    "approval-rejected",
    "approval-expired",
    "approval-superseded",
    "approval-consumed",
    "proposal-mismatch",
    "evidence-decision-mismatch",
    "action-mismatch",
    "idempotency-conflict",
    "outbox-full",
]


class _RefusedAssignSectorAuditData(_CommandAuthorizationAuditData):
    action: _AssignSectorAction
    outcome: Literal["refused"]
    reason: _CommandRefusalReason


class _RefusedEscalateRescueAuditData(_CommandAuthorizationAuditData):
    action: _EscalateRescueCommandAction
    outcome: Literal["refused"]
    reason: _CommandRefusalReason


type _AuditDataValue = (
    _NormalizedProposalAuditData
    | _AbstainedProposalAuditData
    | _RefusedProposalAuditData
    | _ContributingEvidenceAuditData
    | _ManualReviewEvidenceAuditData
    | _AbstainedEvidenceAuditData
    | _RejectedEvidenceAuditData
    | _AuthorizedAssignSectorAuditData
    | _AuthorizedEscalateRescueAuditData
    | _RefusedAssignSectorAuditData
    | _RefusedEscalateRescueAuditData
)


class _AuditRecordEvent(_WireModel):
    kind: Literal["auditRecord"]
    event_class: Literal["AUDIT"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _AuditDataValue


type _DashboardEventValue = Annotated[
    _DroneTelemetryEvent
    | _ConnectivityChangedEvent
    | _MissionLifecycleEvent
    | _SectorLifecycleEvent
    | _OperatorCommandEvent
    | _OperatorApprovalEvent
    | _AgentProposalEvent
    | _EvidenceDecisionEvent
    | _SalientObservationEvent
    | _DroneCommandEvent
    | _CommandResultEvent
    | _GatewayResponseEvent
    | _AuditRecordEvent,
    Field(discriminator="kind"),
]
type _TimelineEventValue = Annotated[
    _ConnectivityChangedEvent
    | _MissionLifecycleEvent
    | _SectorLifecycleEvent
    | _OperatorCommandEvent
    | _OperatorApprovalEvent
    | _AgentProposalEvent
    | _EvidenceDecisionEvent
    | _SalientObservationEvent
    | _DroneCommandEvent
    | _CommandResultEvent
    | _GatewayResponseEvent
    | _AuditRecordEvent,
    Field(discriminator="kind"),
]


class _DashboardEvent(RootModel[_DashboardEventValue]):
    model_config = _ROOT_MODEL_CONFIG


class _OrderedDashboardEvent(_WireModel):
    audit_ordinal: _SafePositiveInteger = Field(alias="auditOrdinal")
    event: _DashboardEventValue


class _TimelineOrderedEvent(_WireModel):
    audit_ordinal: _SafePositiveInteger = Field(alias="auditOrdinal")
    event: _TimelineEventValue


class _DashboardEventFrame(_WireModel):
    frame_version: Literal["ordered-dashboard-event-frame/v1"] = Field(alias="frameVersion")
    cursor: _Cursor
    digest: _Digest
    event: _OrderedDashboardEvent


class _Mission(_WireModel):
    identifier: _Identifier
    lifecycle: _MissionLifecycle
    predecessor_identifier: _Identifier | None = Field(alias="predecessorIdentifier")


class _LatestTelemetry(_TelemetryReading):
    pass


class _SimulatedFleetMember(_WireModel):
    identifier: _Identifier
    participation: Literal["SIMULATED"]
    connectivity: _Connectivity
    telemetry: _LatestTelemetry | None


class _DeclaredOnlyFleetMember(_WireModel):
    identifier: _Identifier
    participation: Literal["DECLARED_ONLY"]


type _ReducedFleetMember = Annotated[
    _SimulatedFleetMember | _DeclaredOnlyFleetMember,
    Field(discriminator="participation"),
]


class _ReducedSector(_WireModel):
    identifier: _Identifier
    state: _SectorState
    assigned_member_id: _Identifier | None = Field(alias="assignedMemberId")


class _DashboardReducedState(_WireModel):
    canonicalization_version: _StrictOne = Field(alias="canonicalizationVersion")
    state_version: _StrictOne = Field(alias="stateVersion")
    current_mission: _Mission | None = Field(alias="currentMission")
    fleet: Annotated[list[_ReducedFleetMember], Field(max_length=23)]
    latest_audit_ordinal: _SafeNonNegativeInteger = Field(alias="latestAuditOrdinal")
    sectors: Annotated[list[_ReducedSector], Field(max_length=20)]


class _LiveRun(_WireModel):
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ReplayRun(_WireModel):
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _CurrentRun = Annotated[_LiveRun | _ReplayRun, Field(discriminator="mode")]


class _DashboardSnapshot(_WireModel):
    snapshot_version: Literal["dashboard-snapshot/v1"] = Field(alias="snapshotVersion")
    runtime_id: _Identifier = Field(alias="runtimeId")
    cursor: _Cursor
    digest: _Digest
    latest_event_digest: _Digest | None = Field(alias="latestEventDigest")
    current_run: _CurrentRun | None = Field(alias="currentRun")
    state: _DashboardReducedState
    timeline: Annotated[list[_TimelineOrderedEvent], Field(max_length=256)]

    @model_validator(mode="after")
    def _require_complete_checkpoint(self) -> _DashboardSnapshot:
        """Bind the external event witness to the reduced-state audit ordinal."""
        if (self.state.latest_audit_ordinal == 0) != (self.latest_event_digest is None):
            message = "latestEventDigest must be null if and only if latestAuditOrdinal is zero"
            raise ValueError(message)
        return self


class _DashboardError(_WireModel):
    error_version: Literal["dashboard-error/v1"] = Field(alias="errorVersion")
    error_code: Literal[
        "ASSET_NOT_FOUND",
        "AUTHENTICATION_FAILED",
        "BODY_TOO_LARGE",
        "CANCELLATION_NOT_ESTABLISHED",
        "CANONICAL_JSON_INVALID",
        "DEPENDENCY_UNAVAILABLE",
        "HOST_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "IDEMPOTENCY_KEY_INVALID",
        "INTERNAL_FAILURE",
        "METHOD_NOT_ALLOWED",
        "MODE_INVALID",
        "MODE_UNAVAILABLE",
        "MUTATION_REFUSED",
        "NOT_READY",
        "NO_CURRENT_RUN",
        "OPERATION_CONFLICT",
        "ORIGIN_INVALID",
        "PATH_BODY_MISMATCH",
        "PATH_INVALID",
        "REPLAY_READ_ONLY",
        "REPLAY_SESSION_NOT_FOUND",
        "REQUEST_INVALID",
        "ROUTE_NOT_FOUND",
        "RUN_CONFLICT",
        "SCENARIO_NOT_FOUND",
        "SCENARIO_REVISION_MISMATCH",
        "SCHEMA_INVALID",
        "SSE_CAPACITY_EXCEEDED",
        "UNSUPPORTED_MEDIA_TYPE",
    ] = Field(alias="errorCode")
    message: _NonEmptyString


class _Health(_WireModel):
    health_version: Literal["dashboard-health/v1"] = Field(alias="healthVersion")
    status: Literal["alive"]
    runtime_id: _Identifier = Field(alias="runtimeId")


class _Readiness(_WireModel):
    readiness_version: Literal["dashboard-readiness/v1"] = Field(alias="readinessVersion")
    mode: Literal["degradedLive", "replay"]
    ready: bool
    reasons: Annotated[list[_Kind], Field(max_length=20)]


class _ReplayIntegrity(_WireModel):
    integrity_version: Literal["dashboard-replay-integrity/v1"] = Field(alias="integrityVersion")
    algorithm: Literal["sha256"]
    checksum: _Digest
    expected_final_digest: _Digest = Field(alias="expectedFinalDigest")


class _ReplayBundle(_WireModel):
    bundle_version: Literal["dashboard-replay-bundle/v1"] = Field(alias="bundleVersion")
    session_id: _Identifier = Field(alias="sessionId")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    initial_state: _DashboardReducedState = Field(alias="initialState")
    latest_event_digest: _Digest | None = Field(alias="latestEventDigest")
    events: Annotated[list[_OrderedDashboardEvent], Field(max_length=512)]
    integrity: _ReplayIntegrity

    @model_validator(mode="after")
    def _require_complete_checkpoint(self) -> _ReplayBundle:
        """Bind the external event witness to the initial-state audit ordinal."""
        if (self.initial_state.latest_audit_ordinal == 0) != (self.latest_event_digest is None):
            message = "latestEventDigest must be null if and only if latestAuditOrdinal is zero"
            raise ValueError(message)
        return self


class _ResetRequest(_WireModel):
    pass


class _RosterCounts(_WireModel):
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")


class _LiveResetResponse(_RosterCounts):
    operation_version: Literal["dashboard-reset-response/v1"] = Field(alias="operationVersion")
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")
    predecessor_mission_id: _Identifier = Field(alias="predecessorMissionId")


class _ReplayResetResponse(_RosterCounts):
    operation_version: Literal["dashboard-reset-response/v1"] = Field(alias="operationVersion")
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _ResetResponseValue = Annotated[
    _LiveResetResponse | _ReplayResetResponse,
    Field(discriminator="mode"),
]


class _ResetResponse(RootModel[_ResetResponseValue]):
    model_config = _ROOT_MODEL_CONFIG


class _Vertex(_WireModel):
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _LastKnownLocation(_WireModel):
    label: _NonEmptyString
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _Polygon(_WireModel):
    vertices: Annotated[list[_Vertex], Field(min_length=4, max_length=256)]


class _SectorPolygon(_WireModel):
    identifier: _Identifier
    vertices: Annotated[list[_Vertex], Field(min_length=4, max_length=256)]


class _SimulatedCatalogMember(_WireModel):
    identifier: _Identifier
    participation: Literal["SIMULATED"]


class _DeclaredOnlyCatalogMember(_WireModel):
    identifier: _Identifier
    participation: Literal["DECLARED_ONLY"]
    role: _Kind
    execution_label: Literal["DECLARED ONLY — NOT EXECUTED"] = Field(alias="executionLabel")


type _CatalogMember = Annotated[
    _SimulatedCatalogMember | _DeclaredOnlyCatalogMember,
    Field(discriminator="participation"),
]


class _Scenario(_WireModel):
    identifier: _Identifier
    revision: _StrictOne
    title: _NonEmptyString
    summary: _NonEmptyString
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")
    search_area_square_metres: _SafePositiveInteger = Field(alias="searchAreaSquareMetres")
    last_known_location: _LastKnownLocation = Field(alias="lastKnownLocation")
    search_polygon: _Polygon = Field(alias="searchPolygon")
    sectors: Annotated[list[_SectorPolygon], Field(min_length=20, max_length=20)]
    members: Annotated[list[_CatalogMember], Field(min_length=23, max_length=23)]


class _ScenarioCatalog(_WireModel):
    catalog_version: Literal["scenario-catalog/v1"] = Field(alias="catalogVersion")
    scenarios: Annotated[list[_Scenario], Field(max_length=20)]


class _StartRequest(_WireModel):
    mode: Literal["degradedLive", "replay"]
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")


class _LiveStartResponse(_RosterCounts):
    operation_version: Literal["dashboard-start-response/v1"] = Field(alias="operationVersion")
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ReplayStartResponse(_RosterCounts):
    operation_version: Literal["dashboard-start-response/v1"] = Field(alias="operationVersion")
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _StartResponseValue = Annotated[
    _LiveStartResponse | _ReplayStartResponse,
    Field(discriminator="mode"),
]


class _StartResponse(RootModel[_StartResponseValue]):
    model_config = _ROOT_MODEL_CONFIG


class _StreamOverloaded(_WireModel):
    control_version: Literal["dashboard-stream-overloaded/v1"] = Field(alias="controlVersion")
    reason: Literal["NON_DROPPABLE_BUFFER_FULL"]


class _OperatorCommandRequest(_WireModel):
    mission_id: _Identifier = Field(alias="missionId")
    action: _OperatorCommandAction


class _CommandResponse(_WireModel):
    operation_version: Literal["dashboard-command-response/v1"] = Field(alias="operationVersion")
    mission_id: _Identifier = Field(alias="missionId")
    command_id: _Identifier = Field(alias="commandId")
    event_id: _Identifier = Field(alias="eventId")


class _ProposalDecisionRequest(_WireModel):
    mission_id: _Identifier = Field(alias="missionId")
    proposal_id: _Identifier = Field(alias="proposalId")
    proposal_digest: _Digest = Field(alias="proposalDigest")
    proposal_version: _StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: _Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: _Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: _StrictOne = Field(alias="evidenceDecisionVersion")
    decision: Literal["approve", "reject"]
    action: _EscalateRescueAction


class _ProposalDecisionResponse(_WireModel):
    operation_version: Literal["dashboard-proposal-decision-response/v1"] = Field(
        alias="operationVersion"
    )
    mission_id: _Identifier = Field(alias="missionId")
    proposal_id: _Identifier = Field(alias="proposalId")
    approval_id: _Identifier = Field(alias="approvalId")
    event_id: _Identifier = Field(alias="eventId")
    issued_at: _Instant = Field(alias="issuedAt")


class _ApprovedProposalDecisionResponse(_ProposalDecisionResponse):
    decision: Literal["approve"]
    expires_at: _Instant = Field(alias="expiresAt")


class _RejectedProposalDecisionResponse(_ProposalDecisionResponse):
    decision: Literal["reject"]


type _ProposalDecisionResponseValue = Annotated[
    _ApprovedProposalDecisionResponse | _RejectedProposalDecisionResponse,
    Field(discriminator="decision"),
]


class _ProposalDecisionResponseDocument(RootModel[_ProposalDecisionResponseValue]):
    model_config = _ROOT_MODEL_CONFIG


class _ScenarioControlStartRequest(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ScenarioControlRunStatus(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")
    state: _MissionLifecycle
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")
    completed_tick_count: _SafeNonNegativeInteger = Field(alias="completedTickCount")
    telemetry_publication_count: _SafeNonNegativeInteger = Field(alias="telemetryPublicationCount")


class _ScenarioControlCancelRequest(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ScenarioControlRefusal(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    error_code: Literal[
        "HOST_INVALID",
        "AUTHENTICATION_FAILED",
        "UNSUPPORTED_MEDIA_TYPE",
        "BODY_TOO_LARGE",
        "CANONICAL_JSON_INVALID",
        "SCHEMA_INVALID",
        "PATH_BODY_MISMATCH",
        "RUN_CONFLICT",
        "RUN_NOT_FOUND",
        "CANCELLATION_NOT_ESTABLISHED",
        "SCENARIO_NOT_FOUND",
        "SCENARIO_REVISION_MISMATCH",
        "FLEET_UNAVAILABLE",
        "INTERNAL_FAILURE",
    ] = Field(alias="errorCode")
    message: _NonEmptyString


def _dashboard_schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}dashboard/{name}.schema.json"


def _rpc_schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}rpc/{name}.schema.json"


SERVER_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _dashboard_schema("bootstrap"): _Bootstrap,
        _dashboard_schema("command-response"): _CommandResponse,
        _dashboard_schema("dashboard-event-frame"): _DashboardEventFrame,
        _dashboard_schema("dashboard-event"): _DashboardEvent,
        _dashboard_schema("dashboard-reduced-state"): _DashboardReducedState,
        _dashboard_schema("dashboard-snapshot"): _DashboardSnapshot,
        _dashboard_schema("error"): _DashboardError,
        _dashboard_schema("health"): _Health,
        _dashboard_schema("ordered-dashboard-event"): _OrderedDashboardEvent,
        _dashboard_schema("operator-command-request"): _OperatorCommandRequest,
        _dashboard_schema("proposal-decision-request"): _ProposalDecisionRequest,
        _dashboard_schema("proposal-decision-response"): _ProposalDecisionResponseDocument,
        _dashboard_schema("readiness"): _Readiness,
        _dashboard_schema("replay-bundle"): _ReplayBundle,
        _dashboard_schema("replay-integrity"): _ReplayIntegrity,
        _dashboard_schema("reset-request"): _ResetRequest,
        _dashboard_schema("reset-response"): _ResetResponse,
        _dashboard_schema("scenario-catalog"): _ScenarioCatalog,
        _dashboard_schema("start-request"): _StartRequest,
        _dashboard_schema("start-response"): _StartResponse,
        _dashboard_schema("stream-overloaded"): _StreamOverloaded,
    }
)
CLIENT_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _rpc_schema("scenario-control-cancel-request"): _ScenarioControlCancelRequest,
        _rpc_schema("scenario-control-refusal"): _ScenarioControlRefusal,
        _rpc_schema("scenario-control-run-status"): _ScenarioControlRunStatus,
        _rpc_schema("scenario-control-start-request"): _ScenarioControlStartRequest,
    }
)
FILE_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType({})
BROWSER_ONLY_SCHEMA_IDS = frozenset(
    {
        _dashboard_schema("mutation-outcome"),
        _dashboard_schema("source-signal"),
    }
)


def parse_wire_document(schema_id: str, raw: str | bytes) -> BaseModel:
    """Canonical-decode and strictly validate one dashboard-owned wire document."""
    model = SERVER_MODEL_BY_SCHEMA_ID.get(schema_id) or CLIENT_MODEL_BY_SCHEMA_ID.get(schema_id)
    if model is None:
        message = f"schema is not owned by dashboard API: {schema_id}"
        raise ValueError(message)
    return model.model_validate(canonical.decode(raw), strict=True)
