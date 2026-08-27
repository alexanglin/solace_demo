"""Read-only reconstruction of complete source events and ordered provenance facts."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import Context, digest, source_event_digest
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import (
    SourceEvidenceDecision,
    SourceEvidenceError,
    SourceEvidenceRefusal,
    StoredSourceEvidence,
    StoredSourceEvidenceFact,
    load_source_evidence,
    record_source_evidence,
    record_source_fact_statement,
    source_evidence_statement,
)
from aerial_rescue_store.session import transaction
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
MISSION: Final = "mission-1"
EVENT_ID: Final = "source-event-1"
SOURCE: Final = "urn:aerial-rescue:drone:drone-1"
TOPIC: Final = f"aerial-rescue/v1/{MISSION}/drone/drone-1/event/salient"
EVENT: Final = (
    b'{"correlationid":"correlation-1","data":{"detail":"orange tarp",'
    b'"droneId":"drone-1","latitudeMicrodegrees":47123901,'
    b'"longitudeMicrodegrees":-122653114,"missionId":"mission-1",'
    b'"observation":"thermal-contact"},"datacontenttype":"application/json",'
    b'"dataschema":"https://aerial-rescue.invalid/schemas/v1/payload/'
    b'drone-event-salient.schema.json","id":"source-event-1",'
    b'"sequence":"000000000000001","source":"urn:aerial-rescue:drone:drone-1",'
    b'"specversion":"1.0","subject":"mission-1",'
    b'"time":"2026-08-25T12:00:00.000Z",'
    b'"traceparent":"00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",'
    b'"type":"aerial-rescue.v1.drone.event.salient"}'
)
DOCUMENT: Final = (
    b'{"canonicalizationVersion":1,"evidenceItemId":"evidence-1",'
    b'"origin":"live-sensor","sourceEventId":"source-event-1",'
    b'"sourceId":"sensor-1"}'
)
SOURCE_DIGEST: Final = source_event_digest(decode_envelope(EVENT))
DOCUMENT_MAPPING: Final = canonical.decode(DOCUMENT)
assert isinstance(DOCUMENT_MAPPING, dict)
PROVENANCE_DIGEST: Final = digest(Context.EVIDENCE, DOCUMENT_MAPPING)
INJECTED_FACT_FAILURE: Final = "injected fact insert failure"
SOURCE_EVENT: Final = StoredSourceEvent(
    source=SOURCE,
    event_id=EVENT_ID,
    mission_id=MISSION,
    topic=TOPIC,
    canonical_digest=SOURCE_DIGEST,
    canonical_payload=EVENT,
    observed_at="2026-08-25T12:00:01.000Z",
)
FACT: Final = StoredSourceEvidenceFact(
    evidence_item_id="evidence-1",
    source_id="sensor-1",
    origin=ObservationOrigin.LIVE_SENSOR,
    provenance_digest=PROVENANCE_DIGEST,
    canonical_document=DOCUMENT,
    document=DOCUMENT_MAPPING,
    observed_at="2026-08-25T12:00:00.500Z",
)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy read without opening a connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _row(
    *,
    source: object = SOURCE,
    ordinal: object = 1,
    document: object = DOCUMENT,
    origin: object = "live-sensor",
) -> tuple[object, ...]:
    """Return one joined source-and-provenance row in repository selection order."""
    return (
        source,
        EVENT_ID,
        MISSION,
        TOPIC,
        SOURCE_DIGEST,
        EVENT,
        "2026-08-25T12:00:01.000Z",
        ordinal,
        "evidence-1",
        "sensor-1",
        origin,
        PROVENANCE_DIGEST,
        document,
        "2026-08-25T12:00:00.500Z",
    )


@dataclass
class _Rows:
    """Return scripted joined rows."""

    rows: Sequence[Sequence[object]]

    def all(self) -> Sequence[Sequence[object]]:
        """Return every scripted row in database order."""
        return self.rows

    def one_or_none(self) -> Sequence[object] | None:
        """Return one exact-identity row or no row."""
        return self.rows[0] if self.rows else None


@dataclass
class _Session:
    """Record read statements and expose no write operation."""

    rows: Sequence[Sequence[object]] = ()
    statements: list[str] = field(default_factory=list)

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record the read and return its rows."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


class SourceEvidenceStatementTests(unittest.TestCase):
    def test_one_read_is_mission_bound_ordered_and_bounded_before_mapping(self) -> None:
        # Arrange
        statement = source_evidence_statement(MISSION, EVENT_ID)

        # Act
        rendered = _rendered(statement)

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                "LEFT OUTER JOIN source_evidence_item" in rendered,
                "source_event.mission_id = " in rendered,
                "source_event.event_id = " in rendered,
                "ORDER BY source_event.source, source_evidence_item.ordinal" in rendered,
                "LIMIT" in rendered,
            ),
        )

    def test_fact_insert_binds_complete_document_and_exact_source_identity(self) -> None:
        # Arrange
        statement = record_source_fact_statement(SOURCE_EVENT, 1, FACT)

        # Act
        compiled = DIALECT.statement_compiler(DIALECT, statement)

        # Assert
        self.assertEqual(
            (True, SOURCE, EVENT_ID, DOCUMENT, PROVENANCE_DIGEST),
            (
                str(compiled).startswith("INSERT INTO source_evidence_item "),
                compiled.params["source_event_source"],
                compiled.params["source_event_id"],
                compiled.params["document"],
                compiled.params["provenance_digest"],
            ),
        )


class LoadSourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_canonical_event_and_fact_mapping_are_returned_without_a_write(
        self,
    ) -> None:
        # Arrange
        session = _Session(rows=(_row(),))

        # Act
        loaded = await load_source_evidence(session, MISSION, EVENT_ID)

        # Assert
        assert isinstance(loaded, StoredSourceEvidence)
        fact = loaded.facts[0]
        self.assertEqual(
            (
                TOPIC,
                EVENT,
                "evidence-1",
                "sensor-1",
                ObservationOrigin.LIVE_SENSOR,
                DOCUMENT,
                "source-event-1",
                1,
            ),
            (
                loaded.topic,
                loaded.canonical_event,
                fact.evidence_item_id,
                fact.source_id,
                fact.origin,
                fact.canonical_document,
                fact.document["sourceEventId"],
                len(session.statements),
            ),
        )

    async def test_absent_event_or_event_without_facts_returns_no_authority(self) -> None:
        # Arrange
        no_fact = list(_row())
        no_fact[7:] = [None] * 7
        sessions = (_Session(), _Session(rows=(no_fact,)))

        # Act
        loaded = [await load_source_evidence(session, MISSION, EVENT_ID) for session in sessions]

        # Assert
        self.assertEqual([None, None], loaded)

    async def test_multiple_source_identities_are_refused_instead_of_guessed(self) -> None:
        # Arrange
        rows = (_row(), _row(source="urn:aerial-rescue:drone:drone-2"))

        # Act
        with pytest.raises(SourceEvidenceError) as captured:
            await load_source_evidence(_Session(rows=rows), MISSION, EVENT_ID)

        # Assert
        self.assertEqual(SourceEvidenceRefusal.AMBIGUOUS_SOURCE, captured.value.refusal)

    async def test_malformed_event_fact_or_order_is_refused_without_partial_results(self) -> None:
        # Arrange
        noncanonical = EVENT.replace(b'"correlationid"', b' "correlationid"')
        wrong_document = DOCUMENT.replace(b'"sensor-1"', b'"other-sensor"')
        noncanonical_event_row = list(_row())
        noncanonical_event_row[5] = noncanonical
        cases = (
            (_row(document=b"not-json"), SourceEvidenceRefusal.MALFORMED_FACT),
            (_row(document=wrong_document), SourceEvidenceRefusal.MALFORMED_FACT),
            (_row(origin="unknown"), SourceEvidenceRefusal.MALFORMED_FACT),
            (_row(ordinal=2), SourceEvidenceRefusal.FACT_ORDER),
            (
                tuple(noncanonical_event_row),
                SourceEvidenceRefusal.MALFORMED_EVENT,
            ),
        )

        # Act
        refusals = []
        for row, expected in cases:
            with self.subTest(expected=expected):
                with pytest.raises(SourceEvidenceError) as captured:
                    await load_source_evidence(_Session(rows=(row,)), MISSION, EVENT_ID)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in cases], refusals)

    async def test_more_than_twenty_three_facts_is_refused_by_the_read_bound(self) -> None:
        # Arrange
        rows = tuple(_row(ordinal=ordinal) for ordinal in range(1, 25))

        # Act
        with pytest.raises(SourceEvidenceError) as captured:
            await load_source_evidence(_Session(rows=rows), MISSION, EVENT_ID)

        # Assert
        self.assertEqual(SourceEvidenceRefusal.FACT_LIMIT, captured.value.refusal)

    async def test_malformed_join_shapes_and_partial_fact_sets_fail_closed(self) -> None:
        # Arrange
        inconsistent = list(_row())
        inconsistent[4] = "f" * 64
        malformed_source = list(_row())
        malformed_source[0] = 7
        no_fact = list(_row())
        no_fact[7:] = [None] * 7
        partial_fact = list(_row())
        partial_fact[13] = None
        duplicate_fact = list(_row(ordinal=2))
        malformed_fact = list(_row())
        malformed_fact[8] = 7
        non_mapping_document = canonical.canonical_bytes(["not-a-mapping"])
        cases = (
            ((_row(), tuple(inconsistent)), SourceEvidenceRefusal.MALFORMED_EVENT),
            ((_row()[:-1],), SourceEvidenceRefusal.MALFORMED_EVENT),
            ((tuple(malformed_source),), SourceEvidenceRefusal.MALFORMED_EVENT),
            ((tuple(no_fact), tuple(no_fact)), SourceEvidenceRefusal.MALFORMED_FACT),
            ((tuple(partial_fact),), SourceEvidenceRefusal.MALFORMED_FACT),
            ((_row(), tuple(duplicate_fact)), SourceEvidenceRefusal.MALFORMED_FACT),
            ((tuple(malformed_fact),), SourceEvidenceRefusal.MALFORMED_FACT),
            ((_row(document=non_mapping_document),), SourceEvidenceRefusal.MALFORMED_FACT),
        )

        # Act
        refusals = []
        for rows, expected in cases:
            with self.subTest(expected=expected):
                with pytest.raises(SourceEvidenceError) as captured:
                    await load_source_evidence(_Session(rows=rows), MISSION, EVENT_ID)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in cases], refusals)


def _source_row(event: StoredSourceEvent = SOURCE_EVENT) -> tuple[object, ...]:
    """Return the complete source row in migrated order."""
    return (
        event.source,
        event.event_id,
        event.mission_id,
        event.topic,
        event.canonical_digest,
        event.canonical_payload,
        event.observed_at,
    )


def _fact_row(fact: StoredSourceEvidenceFact = FACT, ordinal: int = 1) -> tuple[object, ...]:
    """Return one exact stored source-fact row in selection order."""
    return (
        SOURCE,
        EVENT_ID,
        ordinal,
        fact.evidence_item_id,
        fact.source_id,
        fact.origin.value,
        fact.provenance_digest,
        fact.canonical_document,
        fact.observed_at,
    )


def _fact_for(evidence_item_id: str, source_id: str) -> StoredSourceEvidenceFact:
    """Build one internally consistent canonical fact for writer behavior tests."""
    document = {
        "canonicalizationVersion": 1,
        "evidenceItemId": evidence_item_id,
        "origin": "live-sensor",
        "sourceEventId": EVENT_ID,
        "sourceId": source_id,
    }
    return StoredSourceEvidenceFact(
        evidence_item_id=evidence_item_id,
        source_id=source_id,
        origin=ObservationOrigin.LIVE_SENSOR,
        provenance_digest=digest(Context.EVIDENCE, document),
        canonical_document=canonical.canonical_bytes(document),
        document=document,
        observed_at="2026-08-25T12:00:00.700Z",
    )


@dataclass
class _WriteSession:
    """Script source/fact insert and comparison reads within one transaction."""

    scalar_values: list[object] = field(default_factory=list)
    selected_rows: list[Sequence[Sequence[object]]] = field(default_factory=list)
    fail_fact_insert: int | None = None
    statements: list[str] = field(default_factory=list)
    transaction_calls: list[str] = field(default_factory=list)
    fact_inserts: int = 0

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Record one source insert and return its scripted identity."""
        self.statements.append(_rendered(statement))
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def execute(self, statement: ClauseElement, /) -> _Rows:
        """Record a fact insert or return the next exact comparison row set."""
        rendered = _rendered(statement)
        self.statements.append(rendered)
        if rendered.startswith("INSERT INTO source_evidence_item "):
            self.fact_inserts += 1
            if self.fact_inserts == self.fail_fact_insert:
                raise RuntimeError(INJECTED_FACT_FAILURE)
            return _Rows(())
        rows = self.selected_rows.pop(0) if self.selected_rows else ()
        return _Rows(rows)

    async def commit(self) -> None:
        """Record the caller-owned transaction commit."""
        self.transaction_calls.append("commit")

    async def rollback(self) -> None:
        """Record rollback of every partial write."""
        self.transaction_calls.append("rollback")

    async def close(self) -> None:
        """Record session release."""
        self.transaction_calls.append("close")


class RecordSourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_source_and_complete_ordered_fact_set_are_stored_on_one_session(self) -> None:
        # Arrange
        second_document = {
            "canonicalizationVersion": 1,
            "evidenceItemId": "evidence-2",
            "origin": "live-model",
            "sourceEventId": EVENT_ID,
            "sourceId": "model-1",
        }
        second = StoredSourceEvidenceFact(
            evidence_item_id="evidence-2",
            source_id="model-1",
            origin=ObservationOrigin.LIVE_MODEL,
            provenance_digest=digest(Context.EVIDENCE, second_document),
            canonical_document=canonical.canonical_bytes(second_document),
            document=second_document,
            observed_at="2026-08-25T12:00:00.600Z",
        )
        session = _WriteSession(scalar_values=[EVENT_ID])

        # Act
        decision = await record_source_evidence(session, SOURCE_EVENT, (FACT, second))

        # Assert
        fact_statements = [
            statement
            for statement in session.statements
            if statement.startswith("INSERT INTO source_evidence_item ")
        ]
        self.assertEqual(
            (SourceEvidenceDecision.STORED, 2, True, True),
            (
                decision,
                len(fact_statements),
                "ordinal" in fact_statements[0],
                "ordinal" in fact_statements[1],
            ),
        )

    async def test_exact_source_and_fact_set_duplicate_is_idempotent_and_writes_no_fact(
        self,
    ) -> None:
        # Arrange
        session = _WriteSession(
            selected_rows=[(_source_row(),), (_fact_row(),)],
        )

        # Act
        decision = await record_source_evidence(session, SOURCE_EVENT, (FACT,))

        # Assert
        self.assertEqual(
            (SourceEvidenceDecision.DUPLICATE, 0, 3),
            (decision, session.fact_inserts, len(session.statements)),
        )

    async def test_changed_digest_or_fact_set_is_a_conflict_without_fact_writes(self) -> None:
        # Arrange
        changed_fact = _fact_for("evidence-1", "sensor-2")
        sessions = (
            _WriteSession(
                selected_rows=[(_source_row(replace(SOURCE_EVENT, canonical_digest="f" * 64)),)]
            ),
            _WriteSession(selected_rows=[(_source_row(),), (_fact_row(changed_fact),)]),
        )
        requests = ((SOURCE_EVENT, (FACT,)), (SOURCE_EVENT, (FACT,)))

        # Act
        refusals = []
        for session, request in zip(sessions, requests, strict=True):
            with self.subTest(statements=session.statements):
                with pytest.raises(SourceEvidenceError) as captured:
                    await record_source_evidence(session, *request)
                refusals.append((captured.value.refusal, session.fact_inserts))

        # Assert
        self.assertEqual(
            [(SourceEvidenceRefusal.IDENTITY_CONFLICT, 0)] * 2,
            refusals,
        )

    async def test_capacity_or_form_refusal_occurs_before_any_database_io(self) -> None:
        # Arrange
        invalid = (
            ((), SourceEvidenceRefusal.FACT_COUNT),
            ((FACT,) * 24, SourceEvidenceRefusal.FACT_COUNT),
            ((replace(FACT, provenance_digest="f" * 64),), SourceEvidenceRefusal.MALFORMED_FACT),
            (
                (replace(FACT, origin=cast("ObservationOrigin", "live-sensor")),),
                SourceEvidenceRefusal.MALFORMED_FACT,
            ),
            (
                (replace(FACT, document=cast("Mapping[str, object]", ["not-a-mapping"])),),
                SourceEvidenceRefusal.MALFORMED_FACT,
            ),
        )

        # Act
        refusals = []
        statement_counts = []
        for facts, expected in invalid:
            session = _WriteSession()
            with self.subTest(expected=expected):
                with pytest.raises(SourceEvidenceError) as captured:
                    await record_source_evidence(session, SOURCE_EVENT, facts)
                refusals.append(captured.value.refusal)
                statement_counts.append(len(session.statements))

        # Assert
        self.assertEqual(
            ([expected for _, expected in invalid], [0] * len(invalid)),
            (refusals, statement_counts),
        )

    async def test_malformed_runtime_values_are_refused_before_any_database_io(self) -> None:
        # Arrange
        wrong_document = dict(cast("Mapping[str, object]", DOCUMENT_MAPPING))
        wrong_document["unexpected"] = True
        requests: tuple[tuple[StoredSourceEvent, Sequence[StoredSourceEvidenceFact]], ...] = (
            (replace(SOURCE_EVENT, source=cast("str", 7)), (FACT,)),
            (replace(SOURCE_EVENT, canonical_payload=b"not-json"), (FACT,)),
            (SOURCE_EVENT, (FACT, FACT)),
            (SOURCE_EVENT, (cast("StoredSourceEvidenceFact", object()),)),
            (SOURCE_EVENT, (replace(FACT, document=wrong_document),)),
        )
        sessions = tuple(_WriteSession() for _request in requests)

        # Act
        refusals = []
        for session, request in zip(sessions, requests, strict=True):
            with self.subTest(request=request):
                with pytest.raises(SourceEvidenceError) as captured:
                    await record_source_evidence(session, *request)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (
                [SourceEvidenceRefusal.MALFORMED_EVENT] * 2
                + [SourceEvidenceRefusal.MALFORMED_FACT] * 3,
                [0] * len(requests),
            ),
            (refusals, [len(session.statements) for session in sessions]),
        )

    async def test_incomplete_or_malformed_committed_fact_sets_fail_closed(self) -> None:
        # Arrange
        wrong_identity = list(_fact_row())
        wrong_identity[0] = "urn:aerial-rescue:drone:other"
        sessions = (
            _WriteSession(selected_rows=[(_source_row(),), ()]),
            _WriteSession(selected_rows=[(_source_row(),), (tuple(wrong_identity),)]),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(rows=session.selected_rows):
                with pytest.raises(SourceEvidenceError) as captured:
                    await record_source_evidence(session, SOURCE_EVENT, (FACT,))
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [SourceEvidenceRefusal.IDENTITY_CONFLICT, SourceEvidenceRefusal.MALFORMED_FACT],
            refusals,
        )

    async def test_partial_fact_failure_rolls_back_through_the_caller_transaction(self) -> None:
        # Arrange
        session = _WriteSession(scalar_values=[EVENT_ID], fail_fact_insert=2)
        facts = (FACT, _fact_for("evidence-2", "sensor-2"))

        # Act
        with pytest.raises(RuntimeError):
            async with transaction(lambda: cast("AsyncSession", session)) as opened:
                await record_source_evidence(opened, SOURCE_EVENT, facts)

        # Assert
        self.assertEqual(
            (2, ["rollback", "close"]), (session.fact_inserts, session.transaction_calls)
        )


if __name__ == "__main__":
    unittest.main()
