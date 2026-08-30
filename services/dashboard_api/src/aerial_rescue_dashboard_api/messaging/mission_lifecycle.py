"""Deterministic dashboard-owned mission lifecycle events for the application outbox."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.mission import (
    MissionError,
    MissionState,
    event_reaching,
    is_terminal,
    transition,
)
from aerial_rescue_store import STORE_BOUNDARY_ERRORS
from aerial_rescue_store.application_outbox import StagedApplicationEvent

from aerial_rescue_dashboard_api.boundary.errors import ApiError
from aerial_rescue_dashboard_api.messaging.mutations import MutationStamp
from aerial_rescue_dashboard_api.messaging.outbox import PRODUCER
from aerial_rescue_dashboard_api.ports import (
    CurrentRun,
    RunMode,
    ScenarioCancellationNotEstablishedError,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
)

PRODUCER_KIND: Final = "mission-lifecycle"
"""The source level ``envelope.BINDINGS`` requires of this family's producer."""

PUBLISHED_STATES: Final[frozenset[MissionState]] = frozenset(
    {
        MissionState.PLANNED,
        MissionState.SEARCHING,
        MissionState.EXHAUSTED,
        MissionState.ABORTED,
    }
)
"""The four states ``mission-event-lifecycle.schema.json`` admits.

``ESCALATED`` and ``COMPLETED`` are domain states with no committed wire value, so they
have no publishable lifecycle event. Which of the four a given mission may actually reach
is the transition table's decision, not this set's.
"""

_EVENT_PARAMETERS: Final = {"eventType": "lifecycle"}
_EMPTY_HEADERS: Final = canonical.canonical_bytes({})
_IDENTITY_CONTEXT: Final = b"aerial-rescue:mission-lifecycle:v1"
_IDENTITY_SEPARATOR: Final = b"\x00"
_IDENTITY_HEX_LENGTH: Final = 32


class LifecycleRefusal(Enum):
    """Why a mission state cannot become a publishable lifecycle event."""

    UNPUBLISHED_STATE = "the mission state has no committed lifecycle value"
    SEQUENCE = "the dashboard producer sequence is outside the envelope profile"
    ALREADY_WATCHING = "this process already owns a mission-lifecycle observer task"


class MissionLifecycleError(ValueError):
    """A typed refusal carrying no identifier, payload, or authority bytes."""

    def __init__(self, refusal: LifecycleRefusal) -> None:
        """Retain only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


def lifecycle_source(runtime_id: str) -> str:
    """Return the producer source this family's binding pattern requires.

    The ``dashboard-api`` source that carries operator commands and approvals does not
    satisfy it, so mission events are a separate producer stream with its own durable
    high-water. One API process is one epoch, which is what keeps a restarted process from
    colliding with its predecessor's recorded sequence.
    """
    return f"urn:aerial-rescue:{PRODUCER_KIND}:{runtime_id}"


def lifecycle_event_id(mission_id: str, lifecycle: MissionState) -> str:
    """Return the one event identity a mission's arrival at ``lifecycle`` ever has.

    The application outbox's primary key is ``(producer, event_id)`` and staging never
    overwrites an existing identity, so deriving the identity from the mission and its
    target state makes staging idempotent with no second durable authority and no extra
    idempotency kind. Repeating the observation restages nothing.

    This is an identity encoding rather than an integrity claim, exactly as ADR-0140's
    producer digest is. The digest is truncated so the value stays inside the envelope
    profile's identifier bound.
    """
    material = _IDENTITY_CONTEXT + _IDENTITY_SEPARATOR + mission_id.encode("ascii")
    material += _IDENTITY_SEPARATOR + lifecycle.name.encode("ascii")
    digest = hashlib.sha256(material).hexdigest()[:_IDENTITY_HEX_LENGTH]
    return f"event-{digest}"


class MissionLifecycleEvents:
    """Build this API process's mission-lifecycle publications.

    The runtime identity, stamp source, and schema registry are fixed for one process, so
    they are bound once rather than threaded through every call. What varies per call is
    only the mission, its run, and the state it reached.
    """

    def __init__(
        self,
        *,
        runtime_id: str,
        stamps: Callable[[], MutationStamp],
        schemas: PayloadSchemaExecutor,
    ) -> None:
        """Bind the process epoch, trusted clock and sequence, and payload validator."""
        self._source = lifecycle_source(runtime_id)
        self._stamps = stamps
        self._schemas = schemas

    @property
    def source(self) -> str:
        """Expose the producer source so composition and tests need no second derivation."""
        return self._source

    def build(
        self,
        mission_id: str,
        run_id: str,
        lifecycle: MissionState,
    ) -> StagedApplicationEvent:
        """Build one exact mission-lifecycle publication, revalidated before it is staged.

        Args:
            mission_id: The mission whose lifecycle changed; also the envelope subject.
            run_id: The run that produced the change, carried as the correlation identity.
            lifecycle: The state the mission has reached.

        Returns:
            One staged application event whose payload is the canonical envelope.

        Raises:
            MissionLifecycleError: With ``UNPUBLISHED_STATE`` for a state the committed
                schema cannot carry, or ``SEQUENCE`` for a sequence outside the envelope
                profile.
        """
        if lifecycle not in PUBLISHED_STATES:
            raise MissionLifecycleError(LifecycleRefusal.UNPUBLISHED_STATE)
        stamp = self._stamps()
        sequence = sequence_text(stamp.sequence)
        if sequence is None:
            raise MissionLifecycleError(LifecycleRefusal.SEQUENCE)
        topic = Topic(Family.MISSION_EVENT, mission_id, dict(_EVENT_PARAMETERS))
        kind = event_type(topic)
        binding = binding_for(kind)
        payload: dict[str, object] = {"missionId": mission_id, "lifecycle": lifecycle.name}
        self._schemas.validate(binding.dataschema, payload)
        event_id = lifecycle_event_id(mission_id, lifecycle)
        document: dict[str, object] = {
            "specversion": "1.0",
            "id": event_id,
            "source": self._source,
            "type": kind,
            "subject": mission_id,
            "time": stamp.occurred_at,
            "datacontenttype": "application/json",
            "dataschema": binding.dataschema,
            "data": payload,
            "sequence": sequence,
            "correlationid": run_id,
            "traceparent": stamp.traceparent,
        }
        envelope = parse_envelope(document)
        check_topic_binding(envelope, topic)
        return StagedApplicationEvent(
            producer=PRODUCER,
            event_id=event_id,
            family=topic.family.outbox_family,
            topic=format_topic(topic),
            headers=_EMPTY_HEADERS,
            payload=canonical.canonical_bytes(envelope_document(envelope)),
            traceparent=stamp.traceparent,
            tracestate=None,
            correlation_id=run_id,
            causation_id=None,
            staged_at=stamp.occurred_at,
        )


_UNAVAILABLE: Final = (
    ApiError,
    ScenarioRunNotFoundError,
    ScenarioCancellationNotEstablishedError,
    *STORE_BOUNDARY_ERRORS,
)
"""Every dependency failure an observation may legitimately meet and simply retry.

The scenario client already redacts its own transport failures into these. The lifecycle
transactions do not, because they are the store's own repository and driver errors, so the
store's declared boundary tuple is included here rather than restated.
"""


class ObservationOutcome(Enum):
    """What one bounded mission-lifecycle observation established."""

    STAGED = "a legal successor was staged for publication"
    CURRENT = "the durable lifecycle already equals the observed state"
    SETTLED = "the mission can accept no further published lifecycle event"
    NOT_APPLICABLE = "no started live run owns an operational mission"
    UNAVAILABLE = "a bounded store or private-control dependency did not answer"


class CurrentRunPort(Protocol):
    """The one durable pointer read this observer needs."""

    async def current_run(self) -> CurrentRun | None:
        """Return the current live or replay pointer, or ``None`` when unset."""


class RunStatusPort(Protocol):
    """The authenticated private status read this observer needs."""

    async def status(self, run_id: str) -> ScenarioRunStatus:
        """Return the private service's authoritative representation of one run."""


class RetainedRun(Protocol):
    """The identity pair a retained predecessor run supplies."""

    @property
    def mission_id(self) -> str | None:
        """Return the retained mission this run belongs to."""

    @property
    def run_id(self) -> str | None:
        """Return the retained live run identity, absent for a replay session."""


class LifecycleTransaction(Protocol):
    """Decide and stage under one exclusive lock on the recorder-owned lifecycle."""

    async def mission_lifecycle(self, mission_id: str) -> str:
        """Read the recorder-owned lifecycle under an exclusive row lock."""

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact publication inside the deciding transaction."""

    async def predecessor_run(self, mission_id: str) -> RetainedRun | None:
        """Return the run a reset retained, reachable only through the mission's link."""


class LifecycleTransactions(Protocol):
    """Open fresh commit-or-rollback mission-lifecycle units of work."""

    def open(self) -> AbstractAsyncContextManager[LifecycleTransaction]:
        """Return one atomic mission-lifecycle transaction."""


class MissionLifecycleObserver:
    """Publish the mission's own lifecycle from the run state private control reports.

    The fleet owns telemetry, connectivity, and sector edges; ADR-0189 gives the dashboard
    API the mission event. The fleet already computes the sweep's ending and the scenario
    service already projects it onto the four wire states, so this observer reads that
    authority rather than re-deriving exhaustion from the sector fold.

    Every decision is the domain transition table's. A state private control reports that
    the table cannot reach from the durable state is refused, not published, so a stale or
    crossed status can never rewrite an operator's mission.
    """

    def __init__(
        self,
        *,
        runs: CurrentRunPort,
        scenario: RunStatusPort,
        transactions: LifecycleTransactions,
        events: MissionLifecycleEvents,
    ) -> None:
        """Retain only injected typed ports and this process's event builder."""
        self._runs = runs
        self._scenario = scenario
        self._transactions = transactions
        self._events = events

    async def observe_once(self) -> ObservationOutcome:
        """Observe one run and stage at most one legal successor.

        Expected store and private-control failures become ``UNAVAILABLE`` rather than
        ending the caller's loop; the next observation reads the same durable state and
        reaches the same decision, so nothing is lost by declining to act now.
        """
        try:
            return await self._observe()
        except _UNAVAILABLE:
            return ObservationOutcome.UNAVAILABLE

    async def _observe(self) -> ObservationOutcome:
        """Run one observation, converting only the transition table's own refusal."""
        selected = await self._runs.current_run()
        identity = _operational_identity(selected)
        if identity is None:
            return ObservationOutcome.NOT_APPLICABLE
        mission_id, run_id = identity
        await self._settle_predecessor(mission_id)
        observed = await self._settled_or_observed(mission_id, run_id)
        if isinstance(observed, ObservationOutcome):
            return observed
        async with self._transactions.open() as unit:
            current = _state(await unit.mission_lifecycle(mission_id))
            if current is observed:
                return ObservationOutcome.CURRENT
            if not _reaches(current, observed):
                return ObservationOutcome.SETTLED
            await unit.stage(self._events.build(mission_id, run_id, observed))
        return ObservationOutcome.STAGED

    async def _settled_or_observed(
        self,
        mission_id: str,
        run_id: str,
    ) -> MissionState | ObservationOutcome:
        """Read private control only while the mission can still reach a new state."""
        if is_terminal(_state(await self._runs_lifecycle(mission_id))):
            return ObservationOutcome.SETTLED
        status = await self._scenario.status(run_id)
        return _state(status.state)

    async def _settle_predecessor(self, mission_id: str) -> None:
        """Publish the ending a reset gave the mission this one replaced.

        Reset is not an edge (ADR-0072): it terminates the current mission and creates a
        new one. The pointer moves with it, so the predecessor is reachable only through
        the successor's immutable link, and its `ABORTED` has no other producer. A
        predecessor that reached its own ending is left exactly as it is, because the
        transition table admits no edge out of one.
        """
        async with self._transactions.open() as unit:
            retained = await unit.predecessor_run(mission_id)
            if retained is None or retained.mission_id is None or retained.run_id is None:
                return
            current = _state(await unit.mission_lifecycle(retained.mission_id))
            if not _reaches(current, MissionState.ABORTED):
                return
            await unit.stage(
                self._events.build(retained.mission_id, retained.run_id, MissionState.ABORTED)
            )

    async def _runs_lifecycle(self, mission_id: str) -> str:
        """Read the durable lifecycle without holding a lock across private control."""
        async with self._transactions.open() as unit:
            return await unit.mission_lifecycle(mission_id)


def _operational_identity(selected: CurrentRun | None) -> tuple[str, str] | None:
    """Return the mission and run of a started live pointer, or ``None``."""
    if selected is None or selected.mode is not RunMode.DEGRADED_LIVE or not selected.started:
        return None
    if selected.mission_id is None or selected.run_id is None:
        return None
    return selected.mission_id, selected.run_id


def _state(name: str) -> MissionState:
    """Return the domain state one stored or reported name denotes."""
    try:
        return MissionState[name]
    except KeyError:
        raise MissionLifecycleError(LifecycleRefusal.UNPUBLISHED_STATE) from None


def _reaches(current: MissionState, observed: MissionState) -> bool:
    """Report whether the transition table admits an edge from ``current`` to ``observed``."""
    event = event_reaching(observed)
    if event is None:
        return False
    try:
        transition(current, event)
    except MissionError:
        return False
    return True


class ObserverPort(Protocol):
    """The one bounded observation the watch schedules."""

    async def observe_once(self) -> ObservationOutcome:
        """Observe one run and stage at most one legal successor."""


class MissionLifecycleWatch:
    """Own this process's one bounded mission-lifecycle observer task.

    The task stages rows; the serving loop publishes them ([ADR-0208]). It holds no broker
    session, so it neither competes with the supervisor's one owned session nor needs its
    readiness. Cancellation is explicit and the shutdown path waits for the task to end.
    """

    def __init__(
        self,
        observer: ObserverPort,
        pause: Callable[[], Awaitable[None]],
        report: Callable[[BaseException], None],
    ) -> None:
        """Retain the observation, the scheduler seam, and the diagnostic sink."""
        self._observer = observer
        self._pause = pause
        self._report = report
        self._task: asyncio.Task[None] | None = None
        self._failures = 0

    @property
    def failures(self) -> int:
        """Return how many observations ended in a failure this process did not expect."""
        return self._failures

    async def start(self) -> None:
        """Start the one owned observer task."""
        if self._task is not None:
            raise MissionLifecycleError(LifecycleRefusal.ALREADY_WATCHING)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel and await the task, surfacing a failure that had already ended it."""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        """Observe, pause, and repeat until the task is cancelled.

        One failed observation must not end the loop. The first live run proved why: an
        observation raised once, the task stopped, and the mission stayed `SEARCHING` for
        the rest of its life while every other part of the chain worked. Nothing surfaced
        it, because a task nobody awaits and nobody drops reports nothing.

        `observe_once` already converts every expected dependency failure into a typed
        outcome, so anything caught here is a defect worth naming. It is reported once per
        failing episode rather than once per observation: a broken dependency at this
        interval would otherwise become one log line every second.
        """
        failing = False
        while True:
            try:
                await self._observer.observe_once()
            except Exception as unexpected:
                self._failures += 1
                if not failing:
                    failing = True
                    self._report(unexpected)
            else:
                failing = False
            await self._pause()
