"""Complete SQLAlchemy metadata for the package-owned durable database schema.

Alembic revisions are immutable history; these :class:`~sqlalchemy.Table` objects are the
current typed repository contract.  Live drift tests compare them with a database migrated to
``head``.  Importing this module emits no DDL and opens no connection.
"""

from __future__ import annotations

import sqlalchemy as sa

from aerial_rescue_store.migration import (
    APPLICATION_OUTBOX_TABLE,
    APPROVAL_BINDING_TABLE,
    APPROVAL_TABLE,
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    BROKER_INBOX_TABLE,
    BROKER_REFUSAL_TABLE,
    COMMAND_OUTBOX_TABLE,
    COMMAND_PROGRESS_TABLE,
    DASHBOARD_BROKER_EVENT_TABLE,
    DASHBOARD_BROKER_SOURCE_TABLE,
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_OPERATION_TABLE,
    DASHBOARD_RUN_TABLE,
    DRONE_COMMAND_EFFECT_TABLE,
    DRONE_COMMAND_RECEIPT_TABLE,
    DRONE_STREAM_STATE_TABLE,
    EVIDENCE_DECISION_TABLE,
    EVIDENCE_ITEM_TABLE,
    IDEMPOTENCY_CLAIM_TABLE,
    PENDING_INVOCATION_TABLE,
    PROPOSAL_TABLE,
    SOURCE_EVENT_TABLE,
    SOURCE_EVIDENCE_ITEM_TABLE,
)

IDENTIFIER_LENGTH = 64
KIND_LENGTH = 32
EVENT_TYPE_LENGTH = 96
"""An audit kind is a CloudEvent type: prefix, family literal, and one KIND level (ADR-0193)."""
SOURCE_LENGTH = 256
TOPIC_LENGTH = 250
INSTANT_LENGTH = 24
DIGEST_LENGTH = 64
TRACEPARENT_LENGTH = 55
TRACESTATE_LENGTH = 512
STATE_LENGTH = 24
AGENT_NAME_LENGTH = 64
UUID_LENGTH = 36
DASHBOARD_SOURCE_LENGTH = 128
MODE_LENGTH = 16
OPERATION_LENGTH = 8
DASHBOARD_STATE_LENGTH = 16
MAXIMUM_PRODUCER_SEQUENCE = 999_999_999_999_999

METADATA = sa.MetaData()
"""The one metadata collection for every table this package may read or write."""

AUDIT_SEQUENCE = sa.Table(
    AUDIT_SEQUENCE_TABLE,
    METADATA,
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("next_ordinal", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint("mission_id", name="pk_audit_sequence"),
    sa.CheckConstraint("next_ordinal >= 1", name="ck_audit_sequence_ordinal_positive"),
)

AUDIT_RECORD = sa.Table(
    AUDIT_RECORD_TABLE,
    METADATA,
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("ordinal", sa.BigInteger(), nullable=False),
    sa.Column("kind", sa.String(EVENT_TYPE_LENGTH), nullable=False),
    sa.Column("occurred_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("mission_id", "ordinal", name="pk_audit_record"),
    sa.CheckConstraint("ordinal >= 1", name="ck_audit_record_ordinal_positive"),
)

APPROVAL = sa.Table(
    APPROVAL_TABLE,
    METADATA,
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("operator_identity", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("issued_wall", sa.String(INSTANT_LENGTH), nullable=False),
    sa.Column("issued_monotonic_milliseconds", sa.BigInteger(), nullable=False),
    sa.Column("time_to_live_milliseconds", sa.BigInteger(), nullable=False),
    sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("proposal_id", name="pk_approval"),
    sa.CheckConstraint(
        "state IN ('requested', 'approved', 'rejected', 'expired', 'superseded', 'executed')",
        name="ck_approval_state_in_protocol",
    ),
    sa.CheckConstraint("time_to_live_milliseconds > 0", name="ck_approval_time_to_live_positive"),
)

IDEMPOTENCY_CLAIM = sa.Table(
    IDEMPOTENCY_CLAIM_TABLE,
    METADATA,
    sa.Column("idempotency_key", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("kind", sa.String(24), nullable=False),
    sa.Column("body_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("result", sa.LargeBinary(), nullable=True),
    sa.Column("claimed_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("idempotency_key", name="pk_idempotency_claim"),
    sa.CheckConstraint(
        "kind IN ('command', 'approval consumption', 'dashboard command', 'dashboard decision')",
        name="ck_idempotency_claim_kind",
    ),
)

COMMAND_OUTBOX = sa.Table(
    COMMAND_OUTBOX_TABLE,
    METADATA,
    sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
    sa.Column("staged_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("command_id", name="pk_command_outbox"),
    sa.CheckConstraint(
        "state IN ('staged', 'reconciliation needed', 'confirmed')",
        name="ck_command_outbox_state",
    ),
)

DASHBOARD_MISSION = sa.Table(
    DASHBOARD_MISSION_TABLE,
    METADATA,
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("scenario_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("scenario_revision", sa.Integer(), nullable=False),
    sa.Column("lifecycle", sa.String(DASHBOARD_STATE_LENGTH), nullable=False),
    sa.Column("predecessor_mission_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.PrimaryKeyConstraint("mission_id", name="pk_dashboard_mission"),
    sa.ForeignKeyConstraint(
        ("predecessor_mission_id",),
        ("dashboard_mission.mission_id",),
        name="fk_dashboard_mission_predecessor",
    ),
    sa.UniqueConstraint("predecessor_mission_id", name="uq_dashboard_mission_one_successor"),
    sa.UniqueConstraint(
        "mission_id",
        "scenario_id",
        "scenario_revision",
        name="uq_dashboard_mission_scenario_identity",
    ),
    sa.CheckConstraint(
        "lifecycle IN ('PLANNED', 'SEARCHING', 'EXHAUSTED', 'ABORTED')",
        name="ck_dashboard_mission_lifecycle",
    ),
    sa.CheckConstraint(
        "scenario_revision >= 1", name="ck_dashboard_mission_scenario_revision_positive"
    ),
    sa.CheckConstraint(
        "predecessor_mission_id IS NULL OR predecessor_mission_id <> mission_id",
        name="ck_dashboard_mission_not_own_predecessor",
    ),
)

DASHBOARD_RUN = sa.Table(
    DASHBOARD_RUN_TABLE,
    METADATA,
    sa.Column("run_identity", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mode", sa.String(MODE_LENGTH), nullable=False),
    sa.Column("scenario_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("scenario_revision", sa.Integer(), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("run_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("session_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("prepared_initial_state", sa.LargeBinary(), nullable=False),
    sa.PrimaryKeyConstraint("run_identity", name="pk_dashboard_run"),
    sa.ForeignKeyConstraint(
        ("mission_id", "scenario_id", "scenario_revision"),
        (
            "dashboard_mission.mission_id",
            "dashboard_mission.scenario_id",
            "dashboard_mission.scenario_revision",
        ),
        name="fk_dashboard_run_mission_scenario",
    ),
    sa.UniqueConstraint("mission_id", name="uq_dashboard_run_mission"),
    sa.UniqueConstraint("run_id", name="uq_dashboard_run_live_identity"),
    sa.UniqueConstraint("session_id", name="uq_dashboard_run_replay_identity"),
    sa.CheckConstraint("mode IN ('degradedLive', 'replay')", name="ck_dashboard_run_mode"),
    sa.CheckConstraint(
        "scenario_revision >= 1", name="ck_dashboard_run_scenario_revision_positive"
    ),
    sa.CheckConstraint(
        "(mode = 'degradedLive' AND mission_id IS NOT NULL AND run_id IS NOT NULL "
        "AND session_id IS NULL AND run_identity = run_id) OR "
        "(mode = 'replay' AND mission_id IS NULL AND run_id IS NULL "
        "AND session_id IS NOT NULL AND run_identity = session_id)",
        name="ck_dashboard_run_identity_for_mode",
    ),
)

DASHBOARD_CURRENT_RUN = sa.Table(
    DASHBOARD_CURRENT_RUN_TABLE,
    METADATA,
    sa.Column("singleton_key", sa.SmallInteger(), nullable=False),
    sa.Column("run_identity", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("singleton_key", name="pk_dashboard_current_run"),
    sa.ForeignKeyConstraint(
        ("run_identity",),
        ("dashboard_run.run_identity",),
        name="fk_dashboard_current_run_run",
    ),
    sa.CheckConstraint("singleton_key = 1", name="ck_dashboard_current_run_singleton"),
)

DASHBOARD_OPERATION = sa.Table(
    DASHBOARD_OPERATION_TABLE,
    METADATA,
    sa.Column("idempotency_key", sa.String(UUID_LENGTH), nullable=False),
    sa.Column("operation_kind", sa.String(OPERATION_LENGTH), nullable=False),
    sa.Column("mode", sa.String(MODE_LENGTH), nullable=False),
    sa.Column("request_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("scenario_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("scenario_revision", sa.Integer(), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("run_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("session_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("predecessor_mission_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("state", sa.String(DASHBOARD_STATE_LENGTH), nullable=False),
    sa.Column("response_status", sa.SmallInteger(), nullable=True),
    sa.Column("response_body", sa.LargeBinary(), nullable=True),
    sa.PrimaryKeyConstraint("idempotency_key", name="pk_dashboard_operation"),
    sa.ForeignKeyConstraint(
        ("predecessor_mission_id",),
        ("dashboard_mission.mission_id",),
        name="fk_dashboard_operation_predecessor",
    ),
    sa.CheckConstraint("operation_kind IN ('start', 'reset')", name="ck_dashboard_operation_kind"),
    sa.CheckConstraint("mode IN ('degradedLive', 'replay')", name="ck_dashboard_operation_mode"),
    sa.CheckConstraint("state IN ('pending', 'completed')", name="ck_dashboard_operation_state"),
    sa.CheckConstraint(
        "idempotency_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
        name="ck_dashboard_operation_uuid4",
    ),
    sa.CheckConstraint(
        "request_digest ~ '^[0-9a-f]{64}$'", name="ck_dashboard_operation_request_digest"
    ),
    sa.CheckConstraint(
        "scenario_revision >= 1", name="ck_dashboard_operation_scenario_revision_positive"
    ),
    sa.CheckConstraint(
        "(mode = 'degradedLive' AND mission_id IS NOT NULL AND run_id IS NOT NULL "
        "AND session_id IS NULL) OR (mode = 'replay' AND mission_id IS NULL "
        "AND run_id IS NULL AND session_id IS NOT NULL)",
        name="ck_dashboard_operation_identity_for_mode",
    ),
    sa.CheckConstraint(
        "(operation_kind = 'start' AND predecessor_mission_id IS NULL) OR "
        "(operation_kind = 'reset' AND ((mode = 'degradedLive' "
        "AND predecessor_mission_id IS NOT NULL) OR (mode = 'replay' "
        "AND predecessor_mission_id IS NULL)))",
        name="ck_dashboard_operation_predecessor_for_kind",
    ),
    sa.CheckConstraint(
        "(state = 'pending' AND response_status IS NULL AND response_body IS NULL) OR "
        "(state = 'completed' AND response_status BETWEEN 100 AND 599 "
        "AND response_body IS NOT NULL)",
        name="ck_dashboard_operation_result_for_state",
    ),
    sa.Index(
        "uq_dashboard_operation_one_pending",
        sa.text("(1)"),
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    ),
)

DASHBOARD_BROKER_SOURCE = sa.Table(
    DASHBOARD_BROKER_SOURCE_TABLE,
    METADATA,
    sa.Column("source", sa.String(DASHBOARD_SOURCE_LENGTH), nullable=False),
    sa.Column("high_water_sequence", sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint("source", name="pk_dashboard_broker_source"),
    sa.CheckConstraint(
        "high_water_sequence IS NULL OR high_water_sequence BETWEEN 0 AND "
        f"{MAXIMUM_PRODUCER_SEQUENCE}",
        name="ck_dashboard_broker_source_sequence_range",
    ),
)

DASHBOARD_BROKER_EVENT = sa.Table(
    DASHBOARD_BROKER_EVENT_TABLE,
    METADATA,
    sa.Column("source", sa.String(DASHBOARD_SOURCE_LENGTH), nullable=False),
    sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_sequence", sa.BigInteger(), nullable=False),
    sa.Column("payload_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("audit_mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("audit_ordinal", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint("source", "event_id", name="pk_dashboard_broker_event"),
    sa.ForeignKeyConstraint(
        ("source",),
        ("dashboard_broker_source.source",),
        name="fk_dashboard_broker_event_source",
    ),
    sa.ForeignKeyConstraint(
        ("audit_mission_id", "audit_ordinal"),
        ("audit_record.mission_id", "audit_record.ordinal"),
        name="fk_dashboard_broker_event_audit",
    ),
    sa.UniqueConstraint(
        "source", "source_sequence", name="uq_dashboard_broker_event_source_sequence"
    ),
    sa.UniqueConstraint(
        "audit_mission_id", "audit_ordinal", name="uq_dashboard_broker_event_audit"
    ),
    sa.CheckConstraint(
        f"source_sequence BETWEEN 0 AND {MAXIMUM_PRODUCER_SEQUENCE}",
        name="ck_dashboard_broker_event_sequence_range",
    ),
    sa.CheckConstraint("audit_ordinal >= 1", name="ck_dashboard_broker_event_ordinal_positive"),
    sa.CheckConstraint(
        "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_dashboard_broker_event_payload_digest"
    ),
)

BROKER_INBOX = sa.Table(
    BROKER_INBOX_TABLE,
    METADATA,
    sa.Column("consumer", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
    sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("canonical_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("result", sa.LargeBinary(), nullable=True),
    sa.Column("processed_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.PrimaryKeyConstraint("consumer", "source", "event_id", name="pk_broker_inbox"),
    sa.CheckConstraint(
        "(result IS NULL AND processed_at IS NULL) OR "
        "(result IS NOT NULL AND processed_at IS NOT NULL)",
        name="ck_broker_inbox_completion",
    ),
    sa.Index(
        "ix_broker_inbox_mission_processed",
        "mission_id",
        "processed_at",
        "consumer",
        "source",
        "event_id",
    ),
)

BROKER_REFUSAL = sa.Table(
    BROKER_REFUSAL_TABLE,
    METADATA,
    sa.Column("consumer", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source", sa.String(SOURCE_LENGTH), nullable=True),
    sa.Column("family", sa.String(KIND_LENGTH), nullable=True),
    sa.Column("channel", sa.String(TOPIC_LENGTH), nullable=False),
    sa.Column("refusal_code", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("raw_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint(
        "consumer",
        "channel",
        "raw_digest",
        name="pk_broker_refusal",
    ),
    sa.CheckConstraint("octet_length(consumer) > 0", name="ck_broker_refusal_consumer"),
    sa.CheckConstraint(
        "source IS NULL OR octet_length(source) > 0",
        name="ck_broker_refusal_source",
    ),
    sa.CheckConstraint(
        "family IS NULL OR octet_length(family) > 0",
        name="ck_broker_refusal_family",
    ),
    sa.CheckConstraint("octet_length(channel) > 0", name="ck_broker_refusal_channel"),
    sa.CheckConstraint(
        "octet_length(refusal_code) > 0",
        name="ck_broker_refusal_code",
    ),
    sa.CheckConstraint(
        "raw_digest ~ '^[0-9a-f]{64}$'",
        name="ck_broker_refusal_digest",
    ),
    sa.Index(
        "ix_broker_refusal_observed",
        "consumer",
        "observed_at",
        "channel",
        "raw_digest",
    ),
)

SOURCE_EVENT = sa.Table(
    SOURCE_EVENT_TABLE,
    METADATA,
    sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
    sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("topic", sa.String(TOPIC_LENGTH), nullable=False),
    sa.Column("canonical_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("canonical_payload", sa.LargeBinary(), nullable=False),
    sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("source", "event_id", name="pk_source_event"),
    sa.CheckConstraint(
        "canonical_digest ~ '^[0-9a-f]{64}$'",
        name="ck_source_event_digest",
    ),
    sa.Index("ix_source_event_mission_event", "mission_id", "event_id", "source"),
)

PENDING_INVOCATION = sa.Table(
    PENDING_INVOCATION_TABLE,
    METADATA,
    sa.Column("invocation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("agent_name", sa.String(AGENT_NAME_LENGTH), nullable=False),
    sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_event_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("invocation_id", name="pk_pending_invocation"),
    sa.CheckConstraint(
        "source_event_digest ~ '^[0-9a-f]{64}$'",
        name="ck_pending_invocation_source_digest",
    ),
)

SOURCE_EVIDENCE_ITEM = sa.Table(
    SOURCE_EVIDENCE_ITEM_TABLE,
    METADATA,
    sa.Column("source_event_source", sa.String(SOURCE_LENGTH), nullable=False),
    sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("ordinal", sa.SmallInteger(), nullable=False),
    sa.Column("evidence_item_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("origin", sa.String(KIND_LENGTH), nullable=False),
    sa.Column("provenance_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("document", sa.LargeBinary(), nullable=False),
    sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint(
        "source_event_source",
        "source_event_id",
        "ordinal",
        name="pk_source_evidence_item",
    ),
    sa.UniqueConstraint(
        "source_event_source",
        "source_event_id",
        "evidence_item_id",
        name="uq_source_evidence_item_identity",
    ),
    sa.ForeignKeyConstraint(
        ("source_event_source", "source_event_id"),
        ("source_event.source", "source_event.event_id"),
        name="fk_source_evidence_item_source_event",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "ordinal BETWEEN 1 AND 23",
        name="ck_source_evidence_item_ordinal",
    ),
    sa.CheckConstraint(
        "origin IN ('live-model', 'live-sensor', 'recorded')",
        name="ck_source_evidence_item_origin",
    ),
    sa.CheckConstraint(
        "provenance_digest ~ '^[0-9a-f]{64}$'",
        name="ck_source_evidence_item_digest",
    ),
)

APPLICATION_OUTBOX = sa.Table(
    APPLICATION_OUTBOX_TABLE,
    METADATA,
    sa.Column("producer", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("family", sa.String(KIND_LENGTH), nullable=False),
    sa.Column("topic", sa.String(TOPIC_LENGTH), nullable=False),
    sa.Column("headers", sa.LargeBinary(), nullable=False),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
    sa.Column("tracestate", sa.String(TRACESTATE_LENGTH), nullable=True),
    sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("staged_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.Column("confirmed_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.PrimaryKeyConstraint("producer", "event_id", name="pk_application_outbox"),
    sa.CheckConstraint(
        "state IN ('staged', 'reconciliation needed', 'confirmed')",
        name="ck_application_outbox_state",
    ),
    sa.CheckConstraint(
        "(state = 'confirmed' AND confirmed_at IS NOT NULL) OR "
        "(state <> 'confirmed' AND confirmed_at IS NULL)",
        name="ck_application_outbox_confirmation",
    ),
    sa.Index("ix_application_outbox_drain", "producer", "state", "staged_at", "event_id"),
)

PROPOSAL = sa.Table(
    PROPOSAL_TABLE,
    METADATA,
    sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_event_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("agent_name", sa.String(AGENT_NAME_LENGTH), nullable=False),
    sa.Column("invocation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_type", sa.String(KIND_LENGTH), nullable=False),
    sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("latitude_microdegrees", sa.BigInteger(), nullable=False),
    sa.Column("longitude_microdegrees", sa.BigInteger(), nullable=False),
    sa.Column("command_type", sa.String(KIND_LENGTH), nullable=False),
    sa.Column("issued_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("proposal_id", name="pk_proposal"),
    sa.UniqueConstraint("proposal_digest", name="uq_proposal_digest"),
    sa.CheckConstraint("sequence >= 0", name="ck_proposal_sequence_nonnegative"),
    sa.CheckConstraint(
        "latitude_microdegrees BETWEEN -90000000 AND 90000000", name="ck_proposal_latitude"
    ),
    sa.CheckConstraint(
        "longitude_microdegrees BETWEEN -180000000 AND 180000000",
        name="ck_proposal_longitude",
    ),
)

EVIDENCE_ITEM = sa.Table(
    EVIDENCE_ITEM_TABLE,
    METADATA,
    sa.Column("evidence_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("source_kind", sa.String(KIND_LENGTH), nullable=False),
    sa.Column("lifecycle", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("provenance_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence_item"),
    sa.ForeignKeyConstraint(
        ("proposal_id",),
        ("proposal.proposal_id",),
        name="fk_evidence_item_proposal",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "source_kind IN ('live-model', 'live-sensor', 'recorded')",
        name="ck_evidence_item_source_kind",
    ),
    sa.CheckConstraint(
        "lifecycle IN ('requested', 'observed', 'validated', 'manual-review', "
        "'contributing', 'abstained', 'rejected')",
        name="ck_evidence_item_lifecycle",
    ),
)

EVIDENCE_DECISION = sa.Table(
    EVIDENCE_DECISION_TABLE,
    METADATA,
    sa.Column("decision_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("decision_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("decision_version", sa.SmallInteger(), nullable=False),
    sa.Column("score_version", sa.SmallInteger(), nullable=True),
    sa.Column("score", sa.SmallInteger(), nullable=True),
    sa.Column("band", sa.String(STATE_LENGTH), nullable=True),
    sa.Column("outcome", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("contributors", sa.LargeBinary(), nullable=True),
    sa.Column("payload", sa.LargeBinary(), nullable=False),
    sa.Column("decided_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint("decision_id", name="pk_evidence_decision"),
    sa.ForeignKeyConstraint(
        ("proposal_id",),
        ("proposal.proposal_id",),
        name="fk_evidence_decision_proposal",
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint("decision_digest", name="uq_evidence_decision_digest"),
    sa.UniqueConstraint("proposal_id", "sequence", name="uq_evidence_decision_proposal_sequence"),
    sa.CheckConstraint(
        "outcome IN ('contributing', 'manual-review', 'abstained', 'rejected')",
        name="ck_evidence_decision_outcome",
    ),
    sa.CheckConstraint(
        "band IS NULL OR band IN ('none', 'weak', 'supported', 'corroborated')",
        name="ck_evidence_decision_band",
    ),
    sa.CheckConstraint(
        "score IS NULL OR score BETWEEN 0 AND 100", name="ck_evidence_decision_score"
    ),
    sa.CheckConstraint(
        "decision_version > 0 AND (score_version IS NULL OR score_version > 0)",
        name="ck_evidence_decision_versions",
    ),
    sa.CheckConstraint("sequence >= 0", name="ck_evidence_decision_sequence"),
    sa.CheckConstraint(
        "(outcome = 'contributing' AND score_version IS NOT NULL AND score IS NOT NULL "
        "AND band IS NOT NULL AND contributors IS NOT NULL) OR "
        "(outcome <> 'contributing' AND score_version IS NULL AND score IS NULL "
        "AND band IS NULL AND contributors IS NULL)",
        name="ck_evidence_decision_branch",
    ),
    sa.Index("ix_evidence_decision_proposal_sequence", "proposal_id", "sequence"),
)

APPROVAL_BINDING = sa.Table(
    APPROVAL_BINDING_TABLE,
    METADATA,
    sa.Column("approval_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("proposal_version", sa.SmallInteger(), nullable=False),
    sa.Column("evidence_decision_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("evidence_decision_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("evidence_decision_version", sa.SmallInteger(), nullable=False),
    sa.Column("decision", sa.String(16), nullable=False),
    sa.Column("action_payload", sa.LargeBinary(), nullable=False),
    sa.Column("decision_runtime_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("authority_runtime_epoch", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("authority_issued_monotonic_milliseconds", sa.BigInteger(), nullable=True),
    sa.Column("expires_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.PrimaryKeyConstraint("approval_id", name="pk_approval_binding"),
    sa.UniqueConstraint("proposal_id", name="uq_approval_binding_proposal"),
    sa.ForeignKeyConstraint(
        ("proposal_id",),
        ("approval.proposal_id",),
        name="fk_approval_binding_approval",
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ("proposal_id",),
        ("proposal.proposal_id",),
        name="fk_approval_binding_proposal",
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ("evidence_decision_id",),
        ("evidence_decision.decision_id",),
        name="fk_approval_binding_evidence_decision",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "proposal_version > 0 AND evidence_decision_version > 0",
        name="ck_approval_binding_versions",
    ),
    sa.CheckConstraint(
        "evidence_decision_digest ~ '^[0-9a-f]{64}$'",
        name="ck_approval_binding_evidence_digest",
    ),
    sa.CheckConstraint(
        "decision IN ('approve', 'reject')",
        name="ck_approval_binding_decision",
    ),
    sa.CheckConstraint(
        "(decision = 'approve' AND expires_at IS NOT NULL) OR "
        "(decision = 'reject' AND expires_at IS NULL)",
        name="ck_approval_binding_expiry",
    ),
    sa.CheckConstraint(
        "octet_length(action_payload) > 0",
        name="ck_approval_binding_action_payload",
    ),
    sa.CheckConstraint(
        "(authority_runtime_epoch IS NULL AND "
        "authority_issued_monotonic_milliseconds IS NULL) OR "
        "(authority_runtime_epoch IS NOT NULL AND "
        "authority_issued_monotonic_milliseconds IS NOT NULL)",
        name="ck_approval_binding_authority_pair",
    ),
)

COMMAND_PROGRESS = sa.Table(
    COMMAND_PROGRESS_TABLE,
    METADATA,
    sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("send_count", sa.SmallInteger(), nullable=False),
    sa.Column("last_sent_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.Column("deadline_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.Column("result_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
    sa.Column("updated_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("command_id", name="pk_command_progress"),
    sa.CheckConstraint(
        "state IN ('accepted', 'in-flight', 'acknowledged', 'succeeded', 'failed', 'abandoned')",
        name="ck_command_progress_state",
    ),
    sa.CheckConstraint(
        "send_count >= 0 AND send_count <= 5", name="ck_command_progress_send_count"
    ),
)

DRONE_COMMAND_RECEIPT = sa.Table(
    DRONE_COMMAND_RECEIPT_TABLE,
    METADATA,
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("command_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("result", sa.LargeBinary(), nullable=True),
    sa.Column("applied_sequence", sa.BigInteger(), nullable=True),
    sa.Column("processed_at", sa.String(INSTANT_LENGTH), nullable=True),
    sa.PrimaryKeyConstraint("drone_id", "command_id", name="pk_drone_command_receipt"),
    sa.CheckConstraint(
        "applied_sequence IS NULL OR applied_sequence >= 0",
        name="ck_drone_command_receipt_sequence",
    ),
    sa.CheckConstraint(
        "(result IS NULL AND applied_sequence IS NULL AND processed_at IS NULL) OR "
        "(result IS NOT NULL AND applied_sequence IS NOT NULL AND processed_at IS NOT NULL)",
        name="ck_drone_command_receipt_completion",
    ),
)

DRONE_STREAM_STATE = sa.Table(
    DRONE_STREAM_STATE_TABLE,
    METADATA,
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("producer", sa.String(SOURCE_LENGTH), nullable=False),
    sa.Column("high_water", sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint("drone_id", name="pk_drone_stream_state"),
    sa.UniqueConstraint("producer", name="uq_drone_stream_state_producer"),
    sa.CheckConstraint(
        "high_water IS NULL OR high_water BETWEEN 0 AND 999999999999999",
        name="ck_drone_stream_state_high_water",
    ),
)

DRONE_COMMAND_EFFECT = sa.Table(
    DRONE_COMMAND_EFFECT_TABLE,
    METADATA,
    sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
    sa.Column("command_digest", sa.String(DIGEST_LENGTH), nullable=False),
    sa.Column("outcome", sa.String(STATE_LENGTH), nullable=False),
    sa.Column("effect_payload", sa.LargeBinary(), nullable=False),
    sa.Column("applied_sequence", sa.BigInteger(), nullable=False),
    sa.Column("applied_at", sa.String(INSTANT_LENGTH), nullable=False),
    sa.PrimaryKeyConstraint("drone_id", "command_id", name="pk_drone_command_effect"),
    sa.UniqueConstraint(
        "drone_id",
        "applied_sequence",
        name="uq_drone_command_effect_applied_sequence",
    ),
    sa.ForeignKeyConstraint(
        ("drone_id", "command_id"),
        ("drone_command_receipt.drone_id", "drone_command_receipt.command_id"),
        name="fk_drone_command_effect_receipt",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "command_digest ~ '^[0-9a-f]{64}$'",
        name="ck_drone_command_effect_digest",
    ),
    sa.CheckConstraint(
        "outcome IN ('succeeded', 'failed')",
        name="ck_drone_command_effect_outcome",
    ),
    sa.CheckConstraint(
        "octet_length(effect_payload) > 0",
        name="ck_drone_command_effect_payload",
    ),
    sa.CheckConstraint(
        "applied_sequence >= 0",
        name="ck_drone_command_effect_sequence",
    ),
)
