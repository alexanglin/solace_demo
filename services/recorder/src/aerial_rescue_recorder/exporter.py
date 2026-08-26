"""Focused durable export command for one exhausted synthetic dashboard mission."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol, TextIO, cast

from aerial_rescue_contracts.view import (
    MissionLifecycle,
    OrderedDashboardEvent,
    ReducerCheckpoint,
)
from aerial_rescue_store import STORE_BOUNDARY_ERRORS, StoreError
from aerial_rescue_store.bounds import SHUTDOWN_GRACE_SECONDS
from aerial_rescue_store.dashboard_events import (
    EventSession,
    StoredDashboardEvent,
    read_event_page,
    recording_watermark,
)
from aerial_rescue_store.dashboard_runs import RunSession, recording_run
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.session import DurableSession, close, create_session_factory, transaction
from aerial_rescue_store.settings import CONTAINER_HOST, DatabaseSettings, database_settings

from aerial_rescue_recorder.database import engine_bounds
from aerial_rescue_recorder.recording import (
    MAX_DOCUMENT_DEPTH,
    MAX_EVENTS,
    MAX_LINE_BYTES,
    MAX_RECORDING_BYTES,
    RecordingError,
    RecordingRefusal,
    checkpoint_from_prepared_bytes,
    export_normalized_recording,
    ordered_event_from_payload,
    validate_recording,
    write_normalized_recording,
)

WILDERNESS_SCENARIO: Final = "wilderness-missing-person"
WILDERNESS_REVISION: Final = 1
DEFAULT_SECRET_ROOT: Final = Path("/run")
_IDENTIFIER: Final = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")


class RecordingExportRefusal(Enum):
    """Why a durable mission selection cannot become the committed synthetic recording."""

    INVALID_SELECTION = "recording selection is invalid"
    SELECTION_NOT_FOUND = "selected synthetic mission and run were not found"
    MISSION_NOT_EXHAUSTED = "selected synthetic mission is not exhausted"
    INCOMPLETE_HISTORY = "selected synthetic mission history is incomplete or exceeds its bound"
    INVALID_EVENT = "selected synthetic mission contains an invalid normalized event"
    OUTPUT_PATH = "recording output directory is unavailable"
    OUTPUT_EXISTS = "normalized recording output already exists"
    STORE_UNAVAILABLE = "durable recording source is unavailable"


class RecordingExportError(ValueError):
    """A structured export refusal that never retains identifiers or rejected payloads."""

    def __init__(self, refusal: RecordingExportRefusal) -> None:
        """Retain the refusal category without any selected identifier or source bytes."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class StoredRecordingSource:
    """One bounded durable source selected by exact mission and run identity."""

    mission_id: str
    run_id: str
    scenario_id: str
    scenario_revision: int
    lifecycle: str
    prepared_initial_state: bytes
    audit_watermark: int
    events: tuple[StoredDashboardEvent, ...]


class RecordingStorePort(Protocol):
    """The only durable read surface needed by the focused export command."""

    async def load(self, mission_id: str, run_id: str) -> StoredRecordingSource | None:
        """Return one exact bounded source, or no matching live run."""


class RecordingSession(EventSession, RunSession, DurableSession, Protocol):
    """The combined store protocol used inside one read-only transaction."""


@dataclass(frozen=True)
class SqlRecordingStore:
    """Load one recording source through the real purpose-specific store repositories."""

    session_factory: Callable[[], RecordingSession]

    async def load(self, mission_id: str, run_id: str) -> StoredRecordingSource | None:
        """Capture one terminal candidate and no more than the recording event bound."""
        try:
            async with transaction(self.session_factory) as session:
                selected = await recording_run(session, mission_id, run_id)
                if selected is None:
                    return None
                watermark = await recording_watermark(session, mission_id)
                events = await read_event_page(session, mission_id, 0, watermark, MAX_EVENTS)
        except STORE_BOUNDARY_ERRORS as unavailable:
            raise RecordingExportError(RecordingExportRefusal.STORE_UNAVAILABLE) from unavailable
        return StoredRecordingSource(
            mission_id=selected.mission_id,
            run_id=selected.run_id,
            scenario_id=selected.scenario_id,
            scenario_revision=selected.scenario_revision,
            lifecycle=selected.lifecycle,
            prepared_initial_state=selected.prepared_initial_state,
            audit_watermark=watermark,
            events=events,
        )


async def export_selected_recording(
    store: RecordingStorePort,
    mission_id: str,
    run_id: str,
    output_directory: Path,
) -> Path:
    """Export one exact exhausted wilderness run without overwriting an existing artifact."""
    _validate_selection_identifiers(mission_id, run_id)
    source = await store.load(mission_id, run_id)
    if source is None:
        raise RecordingExportError(RecordingExportRefusal.SELECTION_NOT_FOUND)
    _validate_source_identity(source, mission_id, run_id)
    if source.lifecycle != MissionLifecycle.EXHAUSTED.name:
        raise RecordingExportError(RecordingExportRefusal.MISSION_NOT_EXHAUSTED)
    initial = _initial_checkpoint(source)
    ordered = _ordered_events(source)
    raw = _complete_recording(source, mission_id, initial, ordered)
    return _publish_recording(raw, output_directory)


def _initial_checkpoint(source: StoredRecordingSource) -> ReducerCheckpoint:
    """Validate the exact prepared bytes and bind their ordinal to the bounded event page."""
    try:
        initial = checkpoint_from_prepared_bytes(source.prepared_initial_state)
    except RecordingError as invalid:
        raise RecordingExportError(RecordingExportRefusal.INVALID_SELECTION) from invalid
    expected_events = source.audit_watermark - initial.state.latest_audit_ordinal
    if expected_events < 0 or expected_events > MAX_EVENTS or expected_events != len(source.events):
        raise RecordingExportError(RecordingExportRefusal.INCOMPLETE_HISTORY)
    return initial


def _ordered_events(source: StoredRecordingSource) -> tuple[OrderedDashboardEvent, ...]:
    """Validate stored canonical payloads and their redundant audit kind projection."""
    ordered: list[OrderedDashboardEvent] = []
    for stored in source.events:
        try:
            candidate = ordered_event_from_payload(stored.audit_ordinal, stored.payload)
        except RecordingError as invalid:
            raise RecordingExportError(RecordingExportRefusal.INVALID_EVENT) from invalid
        if candidate.event.kind != stored.kind:
            raise RecordingExportError(RecordingExportRefusal.INVALID_EVENT)
        ordered.append(candidate)
    return tuple(ordered)


def _complete_recording(
    source: StoredRecordingSource,
    mission_id: str,
    initial: ReducerCheckpoint,
    ordered: Sequence[OrderedDashboardEvent],
) -> bytes:
    """Use the production exporter and require its reconstructed final mission to agree."""
    try:
        raw = export_normalized_recording(
            source.scenario_id,
            source.scenario_revision,
            initial,
            ordered,
        )
        final = validate_recording(raw, return_checkpoint=True)
    except RecordingError as invalid:
        raise RecordingExportError(RecordingExportRefusal.INVALID_EVENT) from invalid
    mission = final.state.current_mission
    if (
        mission is None
        or mission.identifier != mission_id
        or mission.lifecycle is not MissionLifecycle.EXHAUSTED
    ):
        raise RecordingExportError(RecordingExportRefusal.INCOMPLETE_HISTORY)
    return raw


def _publish_recording(raw: bytes, output_directory: Path) -> Path:
    """Map only the two expected atomic-publication refusals into command outcomes."""
    try:
        return write_normalized_recording(raw, output_directory)
    except RecordingError as invalid_output:
        if invalid_output.refusal is RecordingRefusal.RECORDING_OUTPUT_EXISTS:
            refusal = RecordingExportRefusal.OUTPUT_EXISTS
        elif invalid_output.refusal is RecordingRefusal.RECORDING_OUTPUT_PATH:
            refusal = RecordingExportRefusal.OUTPUT_PATH
        else:
            raise
        raise RecordingExportError(refusal) from invalid_output


def _validate_selection_identifiers(mission_id: str, run_id: str) -> None:
    """Refuse traversal, empty, uppercase, and oversized selection values before a store read."""
    if _IDENTIFIER.fullmatch(mission_id) is None or _IDENTIFIER.fullmatch(run_id) is None:
        raise RecordingExportError(RecordingExportRefusal.INVALID_SELECTION)


def _validate_source_identity(
    source: StoredRecordingSource,
    mission_id: str,
    run_id: str,
) -> None:
    """Bind the store result to the requested fixed synthetic scenario and revision."""
    if (
        source.mission_id != mission_id
        or source.run_id != run_id
        or source.scenario_id != WILDERNESS_SCENARIO
        or source.scenario_revision != WILDERNESS_REVISION
    ):
        raise RecordingExportError(RecordingExportRefusal.INVALID_SELECTION)


async def _export_from_database(
    configured: DatabaseSettings,
    mission_id: str,
    run_id: str,
    output_directory: Path,
) -> Path:
    """Own one lazy bounded store pool for the duration of a single export."""
    pool = create_engine(configured, engine_bounds())
    factory = create_session_factory(pool)
    store = SqlRecordingStore(cast("Callable[[], RecordingSession]", factory))
    try:
        return await export_selected_recording(store, mission_id, run_id, output_directory)
    finally:
        await close(pool, SHUTDOWN_GRACE_SECONDS)


def _parse(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_recorder.exporter",
        description="Export one exhausted wilderness simulation to canonical normalized NDJSON.",
        epilog=(
            f"Bounds: {MAX_EVENTS} events, {MAX_RECORDING_BYTES} output bytes, "
            f"{MAX_LINE_BYTES} bytes per canonical line, depth {MAX_DOCUMENT_DEPTH}."
        ),
    )
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-directory", required=True)
    return parser.parse_args(arguments)


def main(
    arguments: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    secret_root: Path = DEFAULT_SECRET_ROOT,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Export once while reporting only a typed refusal and no selected identity or payload."""
    parsed = _parse(arguments)
    supplied = os.environ if environment is None else environment
    try:
        configured = database_settings(supplied, secret_root, host=CONTAINER_HOST)
        asyncio.run(
            _export_from_database(
                configured,
                parsed.mission_id,
                parsed.run_id,
                Path(parsed.output_directory),
            )
        )
    except RecordingExportError as failure:
        error.write(f"FAILED: {failure.refusal.value}\n")
        return 1
    except StoreError:
        error.write(f"FAILED: {RecordingExportRefusal.STORE_UNAVAILABLE.value}\n")
        return 1
    out.write("normalized recording ready\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
