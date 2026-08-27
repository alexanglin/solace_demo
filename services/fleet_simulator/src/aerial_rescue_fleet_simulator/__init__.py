"""Deterministic fleet physics and adapters around domain state machines.

The simulator is a Tier 2 adapter: it applies already-decided observations to the pure
Tier 1 machines in ``aerial_rescue_domain`` and produces the events those observations
imply. It owns no transition table, no topic grammar, no canonicalizer, and no threshold.

Two decisions fix what it does. ``docs/adr/0077`` makes the scenario a frozen value the
composition root supplies, so nothing here reads a file, an environment variable, a broker
message, a clock, or a random source to obtain one. ``docs/adr/0078`` makes one tick one
heartbeat-or-miss observation per drone, folded in ascending drone-identifier order.

Refusals are :class:`aerial_rescue_domain.DomainError` subclasses, the shape that package
documents, so one handler audits every denied value.
"""

from __future__ import annotations

import hashlib
from typing import Final

from aerial_rescue_contracts import TOPIC_NAMESPACE_ROOT
from aerial_rescue_domain import DomainError

URN_SCHEME: Final = "urn"
URN_SEPARATOR: Final = ":"
PRODUCER_KIND: Final = "drone"
RUN_PRODUCER_KIND: Final = "drone-run"
"""The ``producerKind`` level of a simulated drone's CloudEvents ``source``.

One process simulates many producers, so unlike the command gateway the identity in an
event is not this process's broker role. It is the drone, which is what the committed
golden fixture for drone telemetry already fixes:
``fixtures/golden/v1/event/drone-telemetry/baseline.json`` carries
``urn:aerial-rescue:drone:drone-vision-01``.
"""


class FleetSimulatorError(DomainError):
    """A value the fleet simulator refuses, carrying the refusal as structured data."""


def event_source(drone_id: str) -> str:
    """Return the CloudEvents ``source`` one simulated drone publishes under.

    The producer is the drone rather than the process, because the producer is what scopes
    the envelope's ``sequence`` (``docs/CONTRACTS.md``) and each drone reports its own
    stream.
    """
    return URN_SEPARATOR.join((URN_SCHEME, TOPIC_NAMESPACE_ROOT, PRODUCER_KIND, drone_id))


def run_event_source(drone_id: str, mission_id: str) -> str:
    """Return one restart-safe producer stream for a drone's operational mission.

    The envelope source profile permits one bounded identifier level. Hashing the validated
    mission/drone pair keeps that level within its 64-character bound while making a new
    mission a new producer epoch. A restarted fleet can therefore begin its process-local
    sequence at zero without colliding with the durable high-water of an earlier mission.
    """
    material = b"aerial-rescue:drone-run:v1\x00" + mission_id.encode("ascii")
    material += b"\x00" + drone_id.encode("ascii")
    identity = hashlib.sha256(material).hexdigest()
    return URN_SEPARATOR.join((URN_SCHEME, TOPIC_NAMESPACE_ROOT, RUN_PRODUCER_KIND, identity))
