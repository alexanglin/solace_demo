"""Brokerless production composition for authenticated private scenario control."""

from __future__ import annotations

import hmac
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, TextIO, override

import httpx
from fastapi import FastAPI

from aerial_rescue_scenario_service.catalog import RootedScenarioSource, ScenarioCatalogLoader
from aerial_rescue_scenario_service.control import (
    ScenarioControl,
    ScenarioControlError,
)
from aerial_rescue_scenario_service.fleet_client import FleetClientConfig, FleetControlClient
from aerial_rescue_scenario_service.http import ScenarioHttpConfig, create_app, serve
from aerial_rescue_scenario_service.lifecycle import MissionLifecycle

_REQUIRED_SETTINGS: Final = (
    "SCENARIO_CONTROL_SECRET_FILE",
    "FLEET_CONTROL_SECRET_FILE",
    "SCENARIO_ROOT",
)
_MAXIMUM_SECRET_BYTES: Final = 4096
_CONTROL_SECRET_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_HOST: Final = "scenario-service:8081"
_FLEET_BASE_URL: Final = "http://fleet-simulator:8082"
_FLEET_EXPECTED_HOST: Final = "fleet-simulator:8082"
_MAXIMUM_RUNS: Final = 32
_CANCELLATION_BUDGET_SECONDS: Final = 15.0
_MONITOR_INTERVAL_SECONDS: Final = 1.0
_HTTP_CONNECTIONS: Final = 32
_HTTP_KEEPALIVE_CONNECTIONS: Final = 16


class ScenarioConfigurationRefusal(Enum):
    """Why the brokerless scenario process cannot be composed."""

    MISSING_SETTING = "required scenario setting is absent or blank"
    MATERIAL_INVALID = "scenario credential material is unavailable or invalid"
    HOP_IDENTITY_REUSED = "private control hops must not share one bearer"
    SCENARIO_ROOT = "scenario root is unavailable or not a directory"


class ScenarioConfigurationError(ValueError):
    """A redacted configuration refusal retaining only a setting name."""

    def __init__(self, refusal: ScenarioConfigurationRefusal, value: str) -> None:
        """Retain the structured refusal and setting name, never its value."""
        super().__init__(f"{refusal.value}: {value}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True, repr=False)
class ScenarioConfiguration:
    """Validated catalog and private-hop values with credentials redacted."""

    control_secret: str
    fleet_control_secret: str
    scenario_root: Path

    @override
    def __repr__(self) -> str:
        return (
            f"ScenarioConfiguration(scenario_root={self.scenario_root!r}, credentials=<redacted>)"
        )


class _NoBrokerLifecycle:
    """Compatibility observer that deliberately performs no publication or I/O."""

    def publish(self, run_id: str, mission_id: str, lifecycle: MissionLifecycle) -> bytes:
        """Observe no broker fact; fleet owns application event publication."""
        del run_id, mission_id, lifecycle
        return b""


def _read_secret(path_text: str, setting: str) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path_text, flags)
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            raw = stream.read(_MAXIMUM_SECRET_BYTES + 1)
    except OSError as error:
        raise ScenarioConfigurationError(
            ScenarioConfigurationRefusal.MATERIAL_INVALID, setting
        ) from error
    if not stat.S_ISREG(details.st_mode) or details.st_size > _MAXIMUM_SECRET_BYTES:
        raise ScenarioConfigurationError(ScenarioConfigurationRefusal.MATERIAL_INVALID, setting)
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ScenarioConfigurationError(
            ScenarioConfigurationRefusal.MATERIAL_INVALID, setting
        ) from error
    if len(raw) > _MAXIMUM_SECRET_BYTES or _CONTROL_SECRET_PATTERN.fullmatch(value) is None:
        raise ScenarioConfigurationError(ScenarioConfigurationRefusal.MATERIAL_INVALID, setting)
    return value


def configuration(environment: Mapping[str, str]) -> ScenarioConfiguration:
    """Validate only catalog and distinct private-hop inputs, never broker settings."""
    values: dict[str, str] = {}
    for name in _REQUIRED_SETTINGS:
        value = environment.get(name, "").strip()
        if not value:
            raise ScenarioConfigurationError(ScenarioConfigurationRefusal.MISSING_SETTING, name)
        values[name] = value
    control_secret = _read_secret(
        values["SCENARIO_CONTROL_SECRET_FILE"],
        "SCENARIO_CONTROL_SECRET_FILE",
    )
    fleet_control_secret = _read_secret(
        values["FLEET_CONTROL_SECRET_FILE"],
        "FLEET_CONTROL_SECRET_FILE",
    )
    if hmac.compare_digest(control_secret, fleet_control_secret):
        raise ScenarioConfigurationError(
            ScenarioConfigurationRefusal.HOP_IDENTITY_REUSED, "FLEET_CONTROL_SECRET_FILE"
        )
    try:
        scenario_root = Path(values["SCENARIO_ROOT"]).resolve(strict=True)
    except OSError as error:
        raise ScenarioConfigurationError(
            ScenarioConfigurationRefusal.SCENARIO_ROOT, "SCENARIO_ROOT"
        ) from error
    if not scenario_root.is_dir():
        raise ScenarioConfigurationError(
            ScenarioConfigurationRefusal.SCENARIO_ROOT, "SCENARIO_ROOT"
        )
    return ScenarioConfiguration(control_secret, fleet_control_secret, scenario_root)


def _application(
    configured: ScenarioConfiguration,
    http: httpx.Client,
) -> tuple[FastAPI, ScenarioControl, FleetControlClient]:
    """Compose catalog, fleet HTTP, and private routes with no broker capability."""
    fleet = FleetControlClient(
        FleetClientConfig(
            _FLEET_BASE_URL,
            _FLEET_EXPECTED_HOST,
            configured.fleet_control_secret,
        ),
        http,
    )
    control = ScenarioControl(
        ScenarioCatalogLoader(RootedScenarioSource(configured.scenario_root)),
        fleet,
        _NoBrokerLifecycle(),
        _MAXIMUM_RUNS,
        monitor_wait=lambda stop: stop.wait(_MONITOR_INTERVAL_SECONDS),
        cancellation_budget_seconds=_CANCELLATION_BUDGET_SECONDS,
    )
    app = create_app(control, ScenarioHttpConfig(_EXPECTED_HOST, configured.control_secret))
    return (app, control, fleet)


def main(*, environment: Mapping[str, str] | None = None, error: TextIO = sys.stderr) -> int:
    """Run the brokerless listener and close control and HTTP resources."""
    try:
        configured = configuration(os.environ if environment is None else environment)
        http = httpx.Client(
            limits=httpx.Limits(
                max_connections=_HTTP_CONNECTIONS,
                max_keepalive_connections=_HTTP_KEEPALIVE_CONNECTIONS,
            )
        )
        try:
            app, control, _fleet = _application(configured, http)
            try:
                serve(app)
            finally:
                control.close()
        finally:
            http.close()
    except ScenarioConfigurationError, ScenarioControlError:
        error.write("FAILED: scenario service unavailable\n")
        return 1
    return 0
