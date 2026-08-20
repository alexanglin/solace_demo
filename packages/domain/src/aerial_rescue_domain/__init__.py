"""Pure typed domain rules and state machines: no input or output, no clock, no random source.

Every module here refuses a value by raising a subclass of :class:`DomainError`, whose
``refusal`` is an :class:`enum.Enum` member with prose as its value and whose ``value`` is
the offending input -- the shape ``aerial_rescue_contracts`` uses -- so the command gateway
can audit every denied attempt through one handler.
"""

from __future__ import annotations

from enum import Enum


class DomainError(ValueError):
    """A domain rule refused a value, carrying the refusal as structured data."""

    refusal: Enum
    value: object

    def __init__(self, refusal: Enum, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value
