"""Production configuration and composition root for private fleet control."""

from __future__ import annotations

import os
import re
import stat
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final, TextIO, override
from uuid import uuid4

from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    MessagingError,
    open_fleet_session,
)
from aerial_rescue_domain.commands import SendBudget
from fastapi import FastAPI

from aerial_rescue_fleet_simulator.control_plane.control import (
    FleetControl,
    FleetControlError,
    FleetWorker,
    InterruptiblePacer,
)
from aerial_rescue_fleet_simulator.control_plane.http import FleetHttpConfig, create_app, serve
from aerial_rescue_fleet_simulator.service import CountingStamps, IntakeBounds

_BROKER_SETTINGS: Final = (
    "SOLACE_BROKER_URL",
    "SOLACE_BROKER_VPN",
    "TRUST_STORE",
    "SOLACE_BROKER_PASSWORD_FILE",
)
_REQUIRED_SETTINGS: Final = (
    *_BROKER_SETTINGS,
    "FLEET_CONTROL_SECRET_FILE",
    "FLEET_COMMAND_INTAKE_MODE",
)
_MAXIMUM_SECRET_BYTES: Final = 4096
_CONTROL_SECRET_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_HOST: Final = "fleet-simulator:8082"
_MAXIMUM_RUNS: Final = 32
_CANCELLATION_WAIT_SECONDS: Final = 15.0
_COMMAND_SEND_BUDGET: Final = 5
_COMMANDS_PER_DRONE_PER_TICK: Final = 1
_PROCESS_SOURCE_SEQUENCES: Final[dict[str, int]] = {}
_PROCESS_SOURCE_SEQUENCE_LOCK: Final = threading.Lock()


class FleetConfigurationRefusal(Enum):
    """Why the production fleet process cannot be composed."""

    MISSING_SETTING = "required fleet setting is absent or blank"
    MATERIAL_INVALID = "fleet credential material is unavailable or invalid"
    INSECURE_BROKER = "fleet broker transport is not certificate validated"
    INVALID_SETTING = "fleet setting is outside the accepted closed vocabulary"


class FleetConfigurationError(ValueError):
    """A redacted configuration refusal retaining only a setting name."""

    def __init__(self, refusal: FleetConfigurationRefusal, value: str) -> None:
        """Retain the structured refusal and setting name, never its value."""
        super().__init__(f"{refusal.value}: {value}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True, repr=False)
class BrokerConfiguration:
    """Validated publish endpoint and one file-indirected broker credential."""

    endpoint: BrokerEndpoint
    credential: str

    @override
    def __repr__(self) -> str:
        return f"BrokerConfiguration(endpoint={self.endpoint!r}, credential=<redacted>)"


@dataclass(frozen=True, repr=False)
class FleetConfiguration:
    """Validated endpoints with both credentials structurally redacted from repr."""

    broker_endpoint: BrokerEndpoint
    broker_credential: str
    control_secret: str
    command_intake_enabled: bool

    @override
    def __repr__(self) -> str:
        return (
            f"FleetConfiguration(broker_endpoint={self.broker_endpoint!r}, credentials=<redacted>)"
        )


def _read_secret(path_text: str, setting: str, *, control_secret: bool) -> str:
    """Read one bounded regular file without following a final symlink."""
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path_text, flags)
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            raw = stream.read(_MAXIMUM_SECRET_BYTES + 1)
    except OSError as error:
        raise FleetConfigurationError(
            FleetConfigurationRefusal.MATERIAL_INVALID, setting
        ) from error
    if not stat.S_ISREG(details.st_mode) or details.st_size > _MAXIMUM_SECRET_BYTES:
        raise FleetConfigurationError(FleetConfigurationRefusal.MATERIAL_INVALID, setting)
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise FleetConfigurationError(
            FleetConfigurationRefusal.MATERIAL_INVALID, setting
        ) from error
    valid = bool(value) and value.isprintable() and len(raw) <= _MAXIMUM_SECRET_BYTES
    if control_secret:
        valid = valid and _CONTROL_SECRET_PATTERN.fullmatch(value) is not None
    if not valid:
        raise FleetConfigurationError(FleetConfigurationRefusal.MATERIAL_INVALID, setting)
    return value


def _broker_configuration(values: Mapping[str, str]) -> BrokerConfiguration:
    """Resolve the TLS endpoint and bounded broker credential from present settings."""
    if not values["SOLACE_BROKER_URL"].startswith("tcps://"):
        raise FleetConfigurationError(
            FleetConfigurationRefusal.INSECURE_BROKER, "SOLACE_BROKER_URL"
        )
    endpoint = BrokerEndpoint(
        values["SOLACE_BROKER_URL"],
        values["SOLACE_BROKER_VPN"],
        values["TRUST_STORE"],
    )
    credential = _read_secret(
        values["SOLACE_BROKER_PASSWORD_FILE"],
        "SOLACE_BROKER_PASSWORD_FILE",
        control_secret=False,
    )
    return BrokerConfiguration(endpoint, credential)


def broker_configuration(environment: Mapping[str, str]) -> BrokerConfiguration:
    """Resolve only the broker material a publisher-only process actually consumes."""
    values: dict[str, str] = {}
    for name in _BROKER_SETTINGS:
        value = environment.get(name, "").strip()
        if not value:
            raise FleetConfigurationError(FleetConfigurationRefusal.MISSING_SETTING, name)
        values[name] = value
    return _broker_configuration(values)


def configuration(environment: Mapping[str, str]) -> FleetConfiguration:
    """Validate production environment and resolve only file-indirected credentials."""
    values: dict[str, str] = {}
    for name in _REQUIRED_SETTINGS:
        value = environment.get(name, "").strip()
        if not value:
            raise FleetConfigurationError(FleetConfigurationRefusal.MISSING_SETTING, name)
        values[name] = value
    broker = _broker_configuration(values)
    control_secret = _read_secret(
        values["FLEET_CONTROL_SECRET_FILE"],
        "FLEET_CONTROL_SECRET_FILE",
        control_secret=True,
    )
    intake_mode = values["FLEET_COMMAND_INTAKE_MODE"]
    if intake_mode not in {"enabled", "publication-only"}:
        raise FleetConfigurationError(
            FleetConfigurationRefusal.INVALID_SETTING, "FLEET_COMMAND_INTAKE_MODE"
        )
    return FleetConfiguration(
        broker.endpoint,
        broker.credential,
        control_secret,
        command_intake_enabled=intake_mode == "enabled",
    )


def _stamps(run_id: str) -> CountingStamps:
    """Bind run correlation to the process-lifetime counters used by this fleet process."""
    return CountingStamps(
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        correlation_id=run_id,
        sequences=_PROCESS_SOURCE_SEQUENCES,
        sequence_lock=_PROCESS_SOURCE_SEQUENCE_LOCK,
    )


def _application(configured: FleetConfiguration) -> tuple[FastAPI, FleetControl]:
    worker = FleetWorker(
        endpoint=configured.broker_endpoint,
        broker_credential=configured.broker_credential,
        open_broker=open_fleet_session,
        stamp_factory=_stamps,
        send_budget=SendBudget(max_sends=_COMMAND_SEND_BUDGET),
        intake=IntakeBounds(commands_per_drone_per_tick=_COMMANDS_PER_DRONE_PER_TICK),
        pacer_factory=InterruptiblePacer,
        command_intake_enabled=configured.command_intake_enabled,
    )
    control = FleetControl(worker, _MAXIMUM_RUNS, _CANCELLATION_WAIT_SECONDS)
    app = create_app(control, FleetHttpConfig(_EXPECTED_HOST, configured.control_secret))
    return (app, control)


def main(*, environment: Mapping[str, str] | None = None, error: TextIO = sys.stderr) -> int:
    """Run the internal listener and report only a redacted expected failure."""
    try:
        configured = configuration(os.environ if environment is None else environment)
        app, control = _application(configured)
        try:
            serve(app)
        finally:
            control.close()
    except FleetConfigurationError, FleetControlError, MessagingError:
        error.write("FAILED: fleet simulator unavailable\n")
        return 1
    return 0
