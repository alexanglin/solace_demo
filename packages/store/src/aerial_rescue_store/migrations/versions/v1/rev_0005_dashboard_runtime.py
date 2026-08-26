"""Persist the narrow dashboard runtime and recorder ordering facts.

Revision: 0005_dashboard_runtime
Parent: 0004_command_outbox
Created: 2026-08-25

ADR-0113 appends this revision after the released four-revision history. ADR-0127 binds every
live run to its mission's complete scenario identity. Dashboard operations remain separate from
command and approval idempotency; their pending slot, stable identities, and exact response
bytes are the durable recovery boundary for start and reset. Runs preserve their prepared
canonical initial state, and moving the singleton pointer never deletes history.

The recorder tables keep producer-scoped high water separate from the broker identity and its
audit link. The normalized dashboard event itself remains the exact canonical payload of the
linked append-only ``audit_record`` row; this revision does not duplicate it or its derived
ordered-event witness. It stores no dashboard wall-clock metadata: exact operation state/bytes
and per-mission audit ordinals are the authorities the runtime actually consumes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_dashboard_runtime"
down_revision: str | None = "0004_command_outbox"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
UUID_LENGTH = 36
SOURCE_LENGTH = 128
MODE_LENGTH = 16
STATE_LENGTH = 16
OPERATION_LENGTH = 8
DIGEST_LENGTH = 64
SEQUENCE_MAXIMUM = 999_999_999_999_999

MISSION_STATES = ("PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED")
RUN_MODES = ("degradedLive", "replay")
OPERATION_KINDS = ("start", "reset")
OPERATION_STATES = ("pending", "completed")


def _values(values: tuple[str, ...]) -> str:
    """Render a closed spelling set inside this immutable revision."""
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Apply this revision after the current store head."""
    op.create_table(
        "dashboard_mission",
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("scenario_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("scenario_revision", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("predecessor_mission_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.PrimaryKeyConstraint("mission_id", name="pk_dashboard_mission"),
        sa.ForeignKeyConstraint(
            ["predecessor_mission_id"],
            ["dashboard_mission.mission_id"],
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
            f"lifecycle IN ({_values(MISSION_STATES)})",
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
    op.create_table(
        "dashboard_run",
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
            ["mission_id", "scenario_id", "scenario_revision"],
            [
                "dashboard_mission.mission_id",
                "dashboard_mission.scenario_id",
                "dashboard_mission.scenario_revision",
            ],
            name="fk_dashboard_run_mission_scenario",
        ),
        sa.UniqueConstraint("mission_id", name="uq_dashboard_run_mission"),
        sa.UniqueConstraint("run_id", name="uq_dashboard_run_live_identity"),
        sa.UniqueConstraint("session_id", name="uq_dashboard_run_replay_identity"),
        sa.CheckConstraint(f"mode IN ({_values(RUN_MODES)})", name="ck_dashboard_run_mode"),
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
    op.create_table(
        "dashboard_current_run",
        sa.Column("singleton_key", sa.SmallInteger(), nullable=False),
        sa.Column("run_identity", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("singleton_key", name="pk_dashboard_current_run"),
        sa.ForeignKeyConstraint(
            ["run_identity"],
            ["dashboard_run.run_identity"],
            name="fk_dashboard_current_run_run",
        ),
        sa.CheckConstraint("singleton_key = 1", name="ck_dashboard_current_run_singleton"),
    )
    op.create_table(
        "dashboard_operation",
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
        sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("idempotency_key", name="pk_dashboard_operation"),
        sa.ForeignKeyConstraint(
            ["predecessor_mission_id"],
            ["dashboard_mission.mission_id"],
            name="fk_dashboard_operation_predecessor",
        ),
        sa.CheckConstraint(
            f"operation_kind IN ({_values(OPERATION_KINDS)})",
            name="ck_dashboard_operation_kind",
        ),
        sa.CheckConstraint(f"mode IN ({_values(RUN_MODES)})", name="ck_dashboard_operation_mode"),
        sa.CheckConstraint(
            f"state IN ({_values(OPERATION_STATES)})", name="ck_dashboard_operation_state"
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            "[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_dashboard_operation_uuid4",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_dashboard_operation_request_digest",
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
    )
    op.create_index(
        "uq_dashboard_operation_one_pending",
        "dashboard_operation",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_table(
        "dashboard_broker_source",
        sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("high_water_sequence", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("source", name="pk_dashboard_broker_source"),
        sa.CheckConstraint(
            f"high_water_sequence IS NULL OR high_water_sequence BETWEEN 0 AND {SEQUENCE_MAXIMUM}",
            name="ck_dashboard_broker_source_sequence_range",
        ),
    )
    op.create_table(
        "dashboard_broker_event",
        sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("payload_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("audit_mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("audit_ordinal", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("source", "event_id", name="pk_dashboard_broker_event"),
        sa.ForeignKeyConstraint(
            ["source"],
            ["dashboard_broker_source.source"],
            name="fk_dashboard_broker_event_source",
        ),
        sa.ForeignKeyConstraint(
            ["audit_mission_id", "audit_ordinal"],
            ["audit_record.mission_id", "audit_record.ordinal"],
            name="fk_dashboard_broker_event_audit",
        ),
        sa.UniqueConstraint(
            "source", "source_sequence", name="uq_dashboard_broker_event_source_sequence"
        ),
        sa.UniqueConstraint(
            "audit_mission_id", "audit_ordinal", name="uq_dashboard_broker_event_audit"
        ),
        sa.CheckConstraint(
            f"source_sequence BETWEEN 0 AND {SEQUENCE_MAXIMUM}",
            name="ck_dashboard_broker_event_sequence_range",
        ),
        sa.CheckConstraint("audit_ordinal >= 1", name="ck_dashboard_broker_event_ordinal_positive"),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_dashboard_broker_event_payload_digest"
        ),
    )


def downgrade() -> None:
    """Drop only the representation this revision added, in dependency order."""
    op.drop_table("dashboard_broker_event")
    op.drop_table("dashboard_broker_source")
    op.drop_index("uq_dashboard_operation_one_pending", table_name="dashboard_operation")
    op.drop_table("dashboard_operation")
    op.drop_table("dashboard_current_run")
    op.drop_table("dashboard_run")
    op.drop_table("dashboard_mission")
