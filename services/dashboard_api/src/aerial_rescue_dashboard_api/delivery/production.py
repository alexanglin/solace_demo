"""Secret-safe production composition for the Unix-socket dashboard process."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final, override
from urllib.parse import urlsplit

from aerial_rescue_observability.freshness import check_lease, epoch_seconds
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.migrations.runtime import MigrationConfiguration
from aerial_rescue_store.migrations.runtime import configuration as store_configuration
from aerial_rescue_store.session import create_session_factory
from fastapi import FastAPI

from aerial_rescue_dashboard_api.boundary.durable_application import (
    ApplicationPorts,
    SecureIdentifiers,
    create_app,
    fresh_runtime_settings,
)
from aerial_rescue_dashboard_api.console import (
    build_solace_runtime,
    mission_lifecycle_pause,
)
from aerial_rescue_dashboard_api.console import (
    settings_from_environment as solace_settings_from_environment,
)
from aerial_rescue_dashboard_api.delivery.assets import load_built_dashboard
from aerial_rescue_dashboard_api.delivery.server import DASHBOARD_SOCKET, run_unix_socket
from aerial_rescue_dashboard_api.lifecycle import RunMode as LifecycleRunMode
from aerial_rescue_dashboard_api.lifecycle import RuntimeReadiness
from aerial_rescue_dashboard_api.messaging.mission_lifecycle import (
    MissionLifecycleObserver,
    MissionLifecycleWatch,
)
from aerial_rescue_dashboard_api.replay import ReplayFilePort
from aerial_rescue_dashboard_api.scenario_client import ScenarioHttpClient
from aerial_rescue_dashboard_api.store_adapter import SqlStore

SCENARIO_CREDENTIAL_PATH_SETTING: Final = "SCENARIO_CONTROL_SECRET_FILE"
SCENARIO_CONTROL_URL: Final = "SCENARIO_CONTROL_URL"
DASHBOARD_ASSET_ROOT: Final = "DASHBOARD_ASSET_ROOT"
DASHBOARD_SOCKET_SETTING: Final = "DASHBOARD_SOCKET"
RECORDER_READINESS_PATH_SETTING: Final = "RECORDER_READINESS_PATH"
EXPECTED_SCENARIO_CONTROL_URL: Final = "http://scenario-service:8081"
EXPECTED_HOST: Final = "127.0.0.1:8080"
DASHBOARD_ORIGIN: Final = "http://127.0.0.1:8080"
REPLAY_BUNDLE_PATH: Final = Path(
    "/run/aerial-rescue/replay/wilderness-missing-person.r1.replay.json"
)
_REQUIRED: Final = (
    SCENARIO_CREDENTIAL_PATH_SETTING,
    SCENARIO_CONTROL_URL,
    DASHBOARD_ASSET_ROOT,
    DASHBOARD_SOCKET_SETTING,
    RECORDER_READINESS_PATH_SETTING,
)
_MAXIMUM_SECRET_BYTES: Final = 4096
_MINIMUM_SECRET_CHARACTERS: Final = 32


class DashboardConfigRefusal(Enum):
    """Why the production dashboard cannot be composed from its closed environment."""

    MISSING_SETTING = "required dashboard setting is absent or blank"
    INVALID_SETTING = "dashboard setting is outside the accepted production topology"
    MISSING_MATERIAL = "required dashboard secret material is unavailable"


class DashboardConfigError(ValueError):
    """A redacted production refusal that retains only a public setting name."""

    def __init__(self, refusal: DashboardConfigRefusal, value: object) -> None:
        """Retain the structured reason without a secret value or filesystem path."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True, repr=False)
class DashboardConfiguration:
    """Every production input, with mounted secret values structurally redacted."""

    environment: Mapping[str, str] = field(repr=False)
    scenario_url: str
    scenario_secret: str
    asset_root: Path
    socket_path: Path
    replay_path: Path
    recorder_readiness_path: Path
    store: MigrationConfiguration

    @override
    def __repr__(self) -> str:
        """Render public topology and redacted database configuration only."""
        return (
            "DashboardConfiguration("
            f"scenario_url={self.scenario_url!r}, asset_root={self.asset_root!r}, "
            f"socket_path={self.socket_path!r}, replay_path={self.replay_path!r}, "
            f"recorder_readiness_path={self.recorder_readiness_path!r}, "
            f"store={self.store!r}, credentials=<redacted>)"
        )


@dataclass
class ProductionResources:
    """Close private HTTP and database resources after broker shutdown."""

    scenario: ScenarioHttpClient
    store: SqlStore
    closed: bool = field(default=False, init=False)

    async def close(self) -> None:
        """Release both resources idempotently, preserving database cleanup on HTTP failure."""
        if self.closed:
            return
        self.closed = True
        try:
            await self.scenario.close()
        finally:
            await self.store.close()


@dataclass(frozen=True)
class ProductionRuntime:
    """The fully injected application and its idempotent resource owner."""

    application: FastAPI
    resources: ProductionResources
    socket_path: Path


def configuration(environment: Mapping[str, str]) -> DashboardConfiguration:
    """Resolve the exact R9 environment contract without opening network resources."""
    values: dict[str, str] = {}
    for name in _REQUIRED:
        value = environment.get(name, "").strip()
        if not value:
            raise DashboardConfigError(DashboardConfigRefusal.MISSING_SETTING, name)
        values[name] = value
    if values[SCENARIO_CONTROL_URL] != EXPECTED_SCENARIO_CONTROL_URL:
        raise DashboardConfigError(DashboardConfigRefusal.INVALID_SETTING, SCENARIO_CONTROL_URL)
    socket_path = Path(values[DASHBOARD_SOCKET_SETTING])
    if socket_path != Path(DASHBOARD_SOCKET):
        raise DashboardConfigError(DashboardConfigRefusal.INVALID_SETTING, DASHBOARD_SOCKET_SETTING)
    asset_root = Path(values[DASHBOARD_ASSET_ROOT])
    if not asset_root.is_absolute():
        raise DashboardConfigError(DashboardConfigRefusal.INVALID_SETTING, DASHBOARD_ASSET_ROOT)
    recorder_readiness_path = Path(values[RECORDER_READINESS_PATH_SETTING])
    if not recorder_readiness_path.is_absolute():
        raise DashboardConfigError(
            DashboardConfigRefusal.INVALID_SETTING,
            RECORDER_READINESS_PATH_SETTING,
        )
    scenario_secret = _secret(Path(values[SCENARIO_CREDENTIAL_PATH_SETTING]))
    return DashboardConfiguration(
        environment=dict(environment),
        scenario_url=values[SCENARIO_CONTROL_URL],
        scenario_secret=scenario_secret,
        asset_root=asset_root,
        socket_path=socket_path,
        replay_path=REPLAY_BUNDLE_PATH,
        recorder_readiness_path=recorder_readiness_path,
        store=store_configuration(environment),
    )


@dataclass(frozen=True)
class RecorderLeaseReadiness:
    """Read the shared active lease as one degraded-live readiness dependency."""

    path: Path
    epoch_source: Callable[[], int] = epoch_seconds

    async def readiness(self) -> tuple[str, ...]:
        """Return one public reason unless the recorder lease is strict and fresh."""
        refusal = check_lease(self.path, now_epoch_seconds=self.epoch_source())
        return () if refusal is None else ("recorder-capture-unavailable",)


def _secret(path: Path) -> str:
    """Read a bounded regular ASCII secret and redact its path on every refusal."""
    try:
        details = path.lstat()
    except OSError as invalid:
        raise DashboardConfigError(
            DashboardConfigRefusal.MISSING_MATERIAL, SCENARIO_CREDENTIAL_PATH_SETTING
        ) from invalid
    if not stat.S_ISREG(details.st_mode) or details.st_size > _MAXIMUM_SECRET_BYTES:
        raise DashboardConfigError(
            DashboardConfigRefusal.MISSING_MATERIAL, SCENARIO_CREDENTIAL_PATH_SETTING
        )
    try:
        raw = path.read_bytes()
        secret = raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError) as invalid:
        raise DashboardConfigError(
            DashboardConfigRefusal.MISSING_MATERIAL, SCENARIO_CREDENTIAL_PATH_SETTING
        ) from invalid
    if len(secret) < _MINIMUM_SECRET_CHARACTERS or b"\x00" in raw:
        raise DashboardConfigError(
            DashboardConfigRefusal.MISSING_MATERIAL, SCENARIO_CREDENTIAL_PATH_SETTING
        )
    return secret


def compose(configured: DashboardConfiguration) -> ProductionRuntime:
    """Build lazy bounded resources and inject them into the production FastAPI graph."""
    built = load_built_dashboard(configured.asset_root)
    engine = create_engine(configured.store.database, configured.store.bounds)
    store = SqlStore(
        create_session_factory(engine),
        engine,
        configured.store.shutdown_grace_seconds,
    )
    scenario = ScenarioHttpClient(configured.scenario_url, configured.scenario_secret)
    replay = ReplayFilePort(configured.replay_path, store)
    recorder = RecorderLeaseReadiness(configured.recorder_readiness_path)
    settings = fresh_runtime_settings(
        allowed_hosts=frozenset({EXPECTED_HOST}),
        dashboard_origin=DASHBOARD_ORIGIN,
        index_template=built.index_template,
        assets=built.assets,
    )
    solace_settings = solace_settings_from_environment(_solace_environment(configured))
    solace = build_solace_runtime(
        solace_settings,
        runtime_id=settings.runtime_id,
        cursor_secret=settings.bearer,
        readiness=RuntimeReadiness(LifecycleRunMode.DEGRADED_LIVE),
    )
    resources = ProductionResources(scenario, store)
    application = create_app(
        settings,
        ApplicationPorts(
            store=store,
            scenario=scenario,
            replay=replay,
            recorder=recorder,
            identifiers=SecureIdentifiers(),
            resources=resources,
            mutations=solace.mutations,
            broker=solace.broker,
            projection=solace.hub,
            lifecycle_watch=MissionLifecycleWatch(
                MissionLifecycleObserver(
                    runs=store,
                    scenario=scenario,
                    transactions=solace.store.lifecycle,
                    events=solace.lifecycle_events,
                ),
                mission_lifecycle_pause,
            ),
        ),
    )
    return ProductionRuntime(application, resources, configured.socket_path)


def _solace_environment(configured: DashboardConfiguration) -> dict[str, str]:
    """Project the reconciled runtime settings without copying any credential value."""
    environment = dict(configured.environment)
    parsed = urlsplit(configured.scenario_url)
    if not parsed.netloc:
        raise DashboardConfigError(DashboardConfigRefusal.INVALID_SETTING, SCENARIO_CONTROL_URL)
    environment.setdefault("DASHBOARD_ALLOWED_HOSTS", EXPECTED_HOST)
    environment.setdefault("DASHBOARD_ALLOWED_ORIGIN", DASHBOARD_ORIGIN)
    environment.setdefault("DASHBOARD_OPERATOR_ID", "local-operator")
    environment.setdefault("DASHBOARD_SOCKET_PATH", str(configured.socket_path))
    environment.setdefault(
        "SCENARIO_CONTROL_BEARER_FILE",
        environment[SCENARIO_CREDENTIAL_PATH_SETTING],
    )
    environment.setdefault("SCENARIO_CONTROL_HOST", parsed.netloc)
    environment.setdefault("DASHBOARD_REPLAY_ROOT", str(configured.replay_path.parent))
    return environment


def main() -> None:
    """Compose and run only the accepted Unix-socket process, closing on startup failure too."""
    runtime = compose(configuration(os.environ))
    try:
        run_unix_socket(runtime.application, socket_path=str(runtime.socket_path))
    finally:
        if not runtime.resources.closed:
            asyncio.run(runtime.resources.close())
