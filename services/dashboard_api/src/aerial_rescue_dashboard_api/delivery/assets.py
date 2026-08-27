"""Closed in-memory production asset manifest with immutable exact responses."""

from __future__ import annotations

import hashlib
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

_HASHED_ASSET = re.compile(
    r"^(?:[A-Za-z0-9_-]+\.[0-9a-f]{16,64}|[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,64})"
    r"\.[A-Za-z0-9]+$"
)
_BOOTSTRAP_PLACEHOLDER: Final = "<!--DASHBOARD_BOOTSTRAP-->"
_MAXIMUM_INDEX_BYTES: Final = 1024 * 1024
_MAXIMUM_ASSET_BYTES: Final = 8 * 1024 * 1024
_MAXIMUM_ASSET_COUNT: Final = 128
_MEDIA_TYPES: Final = MappingProxyType(
    {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".woff2": "font/woff2",
    }
)


@dataclass(frozen=True)
class Asset:
    """One already-built same-origin asset."""

    body: bytes
    media_type: str

    @property
    def etag(self) -> str:
        """Return a strong content identity without reading another resource."""
        return '"' + hashlib.sha256(self.body).hexdigest() + '"'


class AssetCatalog:
    """Serve only exact content-hashed names committed by the frontend build."""

    def __init__(self, assets: Mapping[str, Asset]) -> None:
        """Snapshot and validate the closed manifest."""
        for name in assets:
            if _HASHED_ASSET.fullmatch(name) is None:
                message = f"asset name is not content-hashed: {name}"
                raise ValueError(message)
        self._assets = MappingProxyType(dict(assets))

    def get(self, name: str) -> Asset | None:
        """Return the exact manifest member without filesystem fallback."""
        return self._assets.get(name)


@dataclass(frozen=True)
class BuiltDashboard:
    """One bounded production index template and its closed immutable assets."""

    index_template: str
    assets: AssetCatalog


def load_built_dashboard(root: Path) -> BuiltDashboard:
    """Load one flat Vite output without following symlinks or serving arbitrary files."""
    index = _index_template(root)
    entries = _asset_entries(root)
    assets: dict[str, Asset] = {}
    for path in entries:
        if _HASHED_ASSET.fullmatch(path.name) is None:
            message = "dashboard asset name is not content-hashed"
            raise ValueError(message)
        media_type = _MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            message = "dashboard asset media type is not accepted"
            raise ValueError(message)
        assets[path.name] = Asset(_read_regular(path, _MAXIMUM_ASSET_BYTES), media_type)
    return BuiltDashboard(index, AssetCatalog(assets))


def _index_template(root: Path) -> str:
    """Create one template insertion point without modifying the built asset files."""
    index = _read_regular(root / "index.html", _MAXIMUM_INDEX_BYTES).decode("utf-8")
    if _BOOTSTRAP_PLACEHOLDER not in index:
        closing_head = "</head>"
        if index.count(closing_head) != 1:
            message = "dashboard index has no unique bootstrap insertion point"
            raise ValueError(message)
        index = index.replace(closing_head, _BOOTSTRAP_PLACEHOLDER + closing_head)
    if index.count(_BOOTSTRAP_PLACEHOLDER) != 1:
        message = "dashboard index must contain exactly one bootstrap placeholder"
        raise ValueError(message)
    return index


def _asset_entries(root: Path) -> tuple[Path, ...]:
    """Return a bounded deterministic flat asset inventory."""
    asset_root = root / "assets"
    try:
        entries = tuple(sorted(asset_root.iterdir(), key=lambda path: path.name.encode()))
    except OSError as invalid:
        message = "dashboard asset directory is unavailable"
        raise ValueError(message) from invalid
    if not entries or len(entries) > _MAXIMUM_ASSET_COUNT:
        message = "dashboard asset count is outside the accepted bound"
        raise ValueError(message)
    return entries


def _read_regular(path: Path, maximum_bytes: int) -> bytes:
    """Read one bounded regular file while rejecting symbolic-link indirection."""
    try:
        details = path.lstat()
    except OSError as invalid:
        message = "dashboard build file is unavailable or outside its bound"
        raise ValueError(message) from invalid
    if not stat.S_ISREG(details.st_mode) or details.st_size > maximum_bytes:
        message = "dashboard build file is unavailable or outside its bound"
        raise ValueError(message)
    try:
        raw = path.read_bytes()
    except OSError as invalid:
        message = "dashboard build file is unavailable or outside its bound"
        raise ValueError(message) from invalid
    if len(raw) > maximum_bytes:
        message = "dashboard build file changed beyond its accepted bound"
        raise ValueError(message)
    return raw
