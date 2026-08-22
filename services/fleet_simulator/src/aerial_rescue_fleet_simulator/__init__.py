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

from aerial_rescue_domain import DomainError


class FleetSimulatorError(DomainError):
    """A value the fleet simulator refuses, carrying the refusal as structured data."""
