"""Explicit brokerless composition and console entry point for scenario control."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import uvicorn
from fastapi import FastAPI

from .catalog import FilesystemScenarioCatalog
from .control import ScenarioCoordinator
from .fleet_http import FleetHttpClient, FleetHttpSettings
from .http_runtime import ServerSettings, create_application

_STARTUP_TIMEOUT_SECONDS: Final = 5.0
_SHUTDOWN_TIMEOUT_SECONDS: Final = 15
_LISTENER_PORT: Final = 8081
_INTERNAL_BIND_HOST: Final = str(ipaddress.IPv4Address(0))
_MAX_SECRET_FILE_BYTES: Final = 129


class SettingsError(RuntimeError):
    """A redacted runtime configuration refusal."""

    def __init__(self, refusal: SettingsRefusal) -> None:
        """Record one closed refusal without a setting value or filesystem path."""
        super().__init__(refusal.value)
        self.refusal = refusal


class SettingsRefusal(StrEnum):
    """Closed runtime-configuration failures safe to render at process startup."""

    MISSING = "required runtime setting is missing"
    MATERIAL_UNAVAILABLE = "private bearer material is unavailable"
    CREDENTIAL_REUSE = "private bearer credentials must be distinct"
    INVALID = "private runtime setting is invalid"


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Only the private HTTP and catalog capabilities this service constructs."""

    server: ServerSettings
    fleet: FleetHttpSettings
    catalog_root: Path


@dataclass(frozen=True, slots=True)
class ListenerOptions:
    """The complete bounded internal Uvicorn listener configuration."""

    host: str
    port: int
    access_log: bool
    proxy_headers: bool
    server_header: bool
    timeout_graceful_shutdown_seconds: int


class ServerRunner(Protocol):
    """The narrow Uvicorn invocation shape the console root needs."""

    def __call__(self, application: FastAPI, options: ListenerOptions) -> None:
        """Run one already-composed ASGI application."""
        ...


def _required(environment: Mapping[str, str], name: str) -> str:
    try:
        value = environment[name]
    except KeyError as error:
        raise SettingsError(SettingsRefusal.MISSING) from error
    if not value:
        raise SettingsError(SettingsRefusal.MISSING)
    return value


def _read_bearer(path: Path) -> str:
    if path.is_symlink():
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE)
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE)
        with resolved.open("rb") as stream:
            raw = stream.read(_MAX_SECRET_FILE_BYTES + 1)
    except (OSError, ValueError) as error:
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE) from error
    if len(raw) > _MAX_SECRET_FILE_BYTES:
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE)
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE) from error


def settings_from_environment(environment: Mapping[str, str]) -> RuntimeSettings:
    """Read exactly the six private-control and catalog inputs, never broker configuration."""
    scenario_host = _required(environment, "SCENARIO_CONTROL_HOST")
    scenario_secret_path = Path(_required(environment, "SCENARIO_CONTROL_BEARER_FILE"))
    catalog_root = Path(_required(environment, "SCENARIO_CATALOG_ROOT"))
    fleet_url = _required(environment, "FLEET_CONTROL_URL")
    fleet_host = _required(environment, "FLEET_CONTROL_HOST")
    fleet_secret_path = Path(_required(environment, "FLEET_CONTROL_BEARER_FILE"))
    if scenario_secret_path == fleet_secret_path:
        raise SettingsError(SettingsRefusal.CREDENTIAL_REUSE)
    scenario_bearer = _read_bearer(scenario_secret_path)
    fleet_bearer = _read_bearer(fleet_secret_path)
    if scenario_bearer == fleet_bearer:
        raise SettingsError(SettingsRefusal.CREDENTIAL_REUSE)
    try:
        server = ServerSettings(
            scenario_host,
            scenario_bearer,
            _STARTUP_TIMEOUT_SECONDS,
            _SHUTDOWN_TIMEOUT_SECONDS,
        )
        fleet = FleetHttpSettings(fleet_url, fleet_host, fleet_bearer)
    except ValueError as error:
        raise SettingsError(SettingsRefusal.INVALID) from error
    return RuntimeSettings(server=server, fleet=fleet, catalog_root=catalog_root)


def build_application(settings: RuntimeSettings) -> FastAPI:
    """Construct catalog, private fleet caller, coordinator, and FastAPI—no broker client."""
    definitions = FilesystemScenarioCatalog(settings.catalog_root)
    fleet = FleetHttpClient(settings.fleet)
    control = ScenarioCoordinator(definitions, fleet)
    return create_application(settings.server, control)


def _run_uvicorn(application: FastAPI, options: ListenerOptions) -> None:
    uvicorn.run(
        application,
        host=options.host,
        port=options.port,
        access_log=options.access_log,
        proxy_headers=options.proxy_headers,
        server_header=options.server_header,
        timeout_graceful_shutdown=options.timeout_graceful_shutdown_seconds,
    )


def main(
    environment: Mapping[str, str] | None = None,
    *,
    runner: ServerRunner | None = None,
) -> None:
    """Compose and run the non-published private listener."""
    selected_environment = os.environ if environment is None else environment
    settings = settings_from_environment(selected_environment)
    application = build_application(settings)
    selected_runner = _run_uvicorn if runner is None else runner
    selected_runner(
        application,
        ListenerOptions(
            host=_INTERNAL_BIND_HOST,
            port=_LISTENER_PORT,
            access_log=False,
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown_seconds=_SHUTDOWN_TIMEOUT_SECONDS,
        ),
    )
