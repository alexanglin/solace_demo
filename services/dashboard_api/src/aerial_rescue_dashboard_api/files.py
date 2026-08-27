"""Closed local scenario, asset, and replay material for dashboard compositions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from aerial_rescue_contracts import canonical

from aerial_rescue_dashboard_api.application import AssetMediaType, AssetOutcome
from aerial_rescue_dashboard_api.wire import parse_wire_document

_CATALOG = "catalog.v1.json"
_DASHBOARD_SCHEMA = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


class DashboardFileError(ValueError):
    """A redacted refusal for committed or recorded local material."""

    def __init__(self) -> None:
        """Expose no path, filename, or file content."""
        super().__init__("dashboard local material is invalid")


@dataclass(frozen=True, slots=True)
class DashboardFileSettings:
    """Exact local roots and per-file byte ceiling selected by composition."""

    scenario_root: Path
    asset_root: Path
    replay_root: Path
    maximum_file_bytes: int

    def __post_init__(self) -> None:
        """Refuse nonpositive or non-integer allocation bounds."""
        if type(self.maximum_file_bytes) is not int or self.maximum_file_bytes <= 0:
            raise DashboardFileError


class FilesystemDashboardData:
    """Validate once, serve finite exact bytes, and perform no outbound I/O."""

    def __init__(self, settings: DashboardFileSettings) -> None:
        """Retain paths without reading them at construction time."""
        self._settings = settings
        self._scenario_root: Path | None = None
        self._asset_root: Path | None = None
        self._replay_root: Path | None = None
        self._catalog = b""
        self._scenarios: dict[tuple[str, int], Mapping[str, object]] = {}
        self._replays: dict[tuple[str, int], bytes] = {}
        self._entrypoint = ""
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return true only inside an explicitly started local-resource epoch."""
        return self._ready

    @property
    def catalog_bytes(self) -> bytes:
        """Return the canonical expanded catalog loaded at startup."""
        self._require_ready()
        return self._catalog

    @property
    def entrypoint(self) -> str:
        """Return the sole validated content-hashed JavaScript entrypoint."""
        if not self._entrypoint:
            raise DashboardFileError
        return self._entrypoint

    async def startup(self) -> None:
        """Validate roots, definitions, expanded catalog, and hashed entrypoint."""
        scenario_root = _root(self._settings.scenario_root)
        asset_root = _root(self._settings.asset_root)
        replay_root = _root(self._settings.replay_root)
        catalog, scenarios = _expanded_catalog(
            scenario_root,
            self._settings.maximum_file_bytes,
        )
        entrypoint = discover_asset_entrypoint(
            asset_root,
            self._settings.maximum_file_bytes,
        )
        replays = _replay_index(replay_root, self._settings.maximum_file_bytes)
        self._scenario_root = scenario_root
        self._asset_root = asset_root
        self._replay_root = replay_root
        self._catalog = catalog
        self._scenarios = scenarios
        self._replays = replays
        self._entrypoint = entrypoint
        self._ready = True

    async def shutdown(self) -> None:
        """Stop new reads without mutating or deleting local material."""
        self._ready = False

    def scenario(self, scenario_id: str, revision: int) -> Mapping[str, object]:
        """Return one validated expanded scenario descriptor."""
        self._require_ready()
        try:
            return self._scenarios[(scenario_id, revision)]
        except KeyError as error:
            raise DashboardFileError from error

    async def asset(self, name: str) -> AssetOutcome | None:
        """Read one exact regular hashed asset below the validated asset root."""
        self._require_ready()
        root = self._asset_root
        if root is None:
            raise DashboardFileError
        suffix = Path(name).suffix
        media_type = _MEDIA_TYPES.get(suffix)
        if media_type is None:
            return None
        path = root / name
        if not _safe_regular(root, path, self._settings.maximum_file_bytes):
            return None
        body = _read(path, self._settings.maximum_file_bytes)
        return AssetOutcome(cast("AssetMediaType", media_type), body)

    async def replay(self, session_id: str) -> bytes:
        """Read and validate one canonical replay bundle with no writer capability."""
        self._require_ready()
        root = self._replay_root
        if root is None:
            raise DashboardFileError
        path = root / f"{session_id}.json"
        if not _safe_regular(root, path, self._settings.maximum_file_bytes):
            raise DashboardFileError
        return _validated_replay(path, self._settings.maximum_file_bytes)

    def replay_for_scenario(self, scenario_id: str, revision: int) -> bytes:
        """Return the sole startup-validated replay for one catalog identity."""
        self._require_ready()
        try:
            return self._replays[(scenario_id, revision)]
        except KeyError as error:
            raise DashboardFileError from error

    def _require_ready(self) -> None:
        if not self._ready:
            raise DashboardFileError


def _root(path: Path) -> Path:
    """Resolve one non-symlink directory without creating it."""
    if path.is_symlink():
        raise DashboardFileError
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise DashboardFileError from error
    if not resolved.is_dir():
        raise DashboardFileError
    return resolved


def discover_asset_entrypoint(asset_root: Path, maximum_file_bytes: int) -> str:
    """Return the sole bounded hashed module without opening a runtime data epoch."""
    if type(maximum_file_bytes) is not int or maximum_file_bytes <= 0:
        raise DashboardFileError
    root = _root(asset_root)
    entrypoints = tuple(
        path for path in root.glob("index-*.js") if _safe_regular(root, path, maximum_file_bytes)
    )
    if len(entrypoints) != 1:
        raise DashboardFileError
    return entrypoints[0].name


def _safe_regular(root: Path, path: Path, maximum: int) -> bool:
    """Return whether one existing non-symlink regular file is bounded below root."""
    if path.is_symlink():
        return False
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError, ValueError:
        return False
    return resolved.parent == root and resolved.is_file() and 0 <= size <= maximum


def _read(path: Path, maximum: int) -> bytes:
    """Read at most one byte beyond the explicit ceiling and fail closed on change."""
    try:
        with path.open("rb") as stream:
            body = stream.read(maximum + 1)
    except OSError as error:
        raise DashboardFileError from error
    if len(body) > maximum:
        raise DashboardFileError
    return body


def _expanded_catalog(
    root: Path,
    maximum: int,
) -> tuple[bytes, dict[tuple[str, int], Mapping[str, object]]]:
    """Verify catalog-pinned definitions and build the closed public projection."""
    catalog = _mapping(canonical.decode(_read(root / _CATALOG, maximum)))
    entries = _sequence(catalog, "scenarios")
    scenarios: dict[tuple[str, int], Mapping[str, object]] = {}
    for entry_value in entries:
        entry = _mapping(entry_value)
        relative = Path(_text(entry, "definitionPath"))
        if relative.is_absolute() or ".." in relative.parts:
            raise DashboardFileError
        path = root / relative
        raw = _read(path, maximum)
        expected = _text(entry, "definitionSha256")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected):
            raise DashboardFileError
        definition = _mapping(canonical.decode(raw))
        scenario = _public_scenario(definition)
        key = (_text(scenario, "identifier"), _integer(scenario, "revision"))
        if key in scenarios:
            raise DashboardFileError
        scenarios[key] = scenario
    document = {"catalogVersion": "scenario-catalog/v1", "scenarios": list(scenarios.values())}
    body = canonical.canonical_bytes(document)
    parse_wire_document(_schema("scenario-catalog"), body)
    return body, scenarios


def _replay_index(root: Path, maximum: int) -> dict[tuple[str, int], bytes]:
    """Validate and index each canonical bundle without constructing a writer."""
    result: dict[tuple[str, int], bytes] = {}
    for path in sorted(root.glob("*.json"), key=lambda item: item.name.encode("ascii")):
        if not _safe_regular(root, path, maximum):
            raise DashboardFileError
        body = _validated_replay(path, maximum)
        document = _mapping(canonical.decode(body))
        if path.stem != _text(document, "sessionId"):
            raise DashboardFileError
        key = (_text(document, "scenarioId"), _integer(document, "scenarioRevision"))
        if key in result:
            raise DashboardFileError
        result[key] = body
    return result


def _validated_replay(path: Path, maximum: int) -> bytes:
    """Return one canonical replay document after its closed schema accepts it."""
    body = _read(path, maximum)
    try:
        value = canonical.decode(body)
        if canonical.canonical_bytes(value) != body:
            raise DashboardFileError
        parse_wire_document(_schema("replay-bundle"), body)
    except (TypeError, ValueError) as error:
        raise DashboardFileError from error
    return body


def _public_scenario(definition: Mapping[str, object]) -> Mapping[str, object]:
    """Project one hash-bound definition into the closed public dashboard shape."""
    members = tuple(_mapping(value) for value in _sequence(definition, "members"))
    public_members: list[dict[str, object]] = []
    for member in members:
        participation = _text(member, "participation")
        if participation == "SIMULATED_DRONE":
            public_members.append(
                {"identifier": _text(member, "identifier"), "participation": "SIMULATED"}
            )
        elif participation == "DECLARED_ONLY":
            public_members.append(
                {
                    "identifier": _text(member, "identifier"),
                    "participation": "DECLARED_ONLY",
                    "role": _text(member, "role"),
                    "executionLabel": _text(member, "executionLabel"),
                }
            )
        else:
            raise DashboardFileError
    simulated = sum(item["participation"] == "SIMULATED" for item in public_members)
    declared_only = len(public_members) - simulated
    return {
        "identifier": _text(definition, "identifier"),
        "revision": _integer(definition, "revision"),
        "title": _text(definition, "title"),
        "summary": _text(definition, "summary"),
        "declaredCount": len(public_members),
        "simulatedCount": simulated,
        "declaredOnlyCount": declared_only,
        "searchAreaSquareMetres": _integer(definition, "searchAreaSquareMetres"),
        "lastKnownLocation": dict(_mapping(definition.get("lastKnownLocation"))),
        "searchPolygon": dict(_mapping(definition.get("searchPolygon"))),
        "sectors": [dict(_mapping(value)) for value in _sequence(definition, "sectors")],
        "members": public_members,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DashboardFileError
    return cast("Mapping[str, object]", value)


def _sequence(document: Mapping[str, object], member: str) -> list[object]:
    value = document.get(member)
    if not isinstance(value, list):
        raise DashboardFileError
    return cast("list[object]", value)


def _text(document: Mapping[str, object], member: str) -> str:
    value = document.get(member)
    if not isinstance(value, str):
        raise DashboardFileError
    return value


def _integer(document: Mapping[str, object], member: str) -> int:
    value = document.get(member)
    if type(value) is not int:
        raise DashboardFileError
    return value


def _schema(name: str) -> str:
    return f"{_DASHBOARD_SCHEMA}{name}.schema.json"
