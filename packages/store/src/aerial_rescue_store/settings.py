"""Where the durable cluster is, who connects to it, and the credential that never travels.

``deploy/compose.yaml`` names the user and the database, ``scripts/broker-secrets.sh`` writes
the password as generated material, and ADR-0003 fixes the driver. This module resolves the
three into one frozen value.

The credential is a member of that value and is **never** a member of the data source name.
That is a structural separation rather than a textual one: a URL carrying a password has to be
escaped correctly and redacted at every place it is logged, and one missed call site publishes
it into a public repository. Here there is no call site to miss, because the engine this value
will be handed to takes the credential as a separate connect argument. Relying instead on the
generator's hexadecimal alphabet to keep a password URL-safe would make a property of this
module a silent coupling to ``scripts/broker-secrets.sh``.

``data_source_name`` interpolates the user, host, and database without percent-encoding, which
holds for every value this project uses because each is a PostgreSQL identifier. It is not a
general URL builder, and a caller that introduces a name outside that alphabet owes the
encoding or an engine built from a structured URL rather than this string.

This module reads a file and nothing else. It opens no connection, reads no clock, and takes
its environment and its deploy directory as arguments so that nothing is read at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, override

from aerial_rescue_store import StoreError

if TYPE_CHECKING:
    from collections.abc import Mapping

POSTGRES_USER_SETTING: Final = "POSTGRES_USER"
POSTGRES_DB_SETTING: Final = "POSTGRES_DB"

CREDENTIAL_FILE: Final = "secrets/postgres-password"
"""The generated credential, relative to the deploy directory."""

GENERATOR_COMMAND: Final = "scripts/broker-secrets.sh"

DRIVER: Final = "postgresql+asyncpg"
"""The dialect and driver ADR-0003 selects; the async driver is not a deployment choice."""

DEFAULT_HOST: Final = "127.0.0.1"
"""The loopback address ``deploy/compose.yaml`` publishes the cluster on."""

CONTAINER_HOST: Final = "postgres"
"""The service name a process inside the Compose network reaches the cluster by."""

DEFAULT_PORT: Final = 5432

REDACTED: Final = "<redacted>"


class SettingsRefusal(Enum):
    """Why the durable target cannot be resolved."""

    MISSING_SETTING = "required environment setting is absent or blank"
    MISSING_MATERIAL = "generated material is absent; run " + GENERATOR_COMMAND


class SettingsError(StoreError):
    """A setting or a generated file this module refuses, carrying the refusal as data."""


@dataclass(frozen=True)
class DatabaseSettings:
    """One durable target: where the cluster is, who connects, and with what credential."""

    host: str
    port: int
    user: str
    database: str
    password: str

    @override
    def __repr__(self) -> str:
        """Render the target without the credential it holds."""
        return (
            f"DatabaseSettings(host={self.host!r}, port={self.port!r}, "
            f"user={self.user!r}, database={self.database!r}, password={REDACTED})"
        )


class DatabaseResolver(Protocol):
    """Resolve one bounded PostgreSQL target without opening a connection."""

    def __call__(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        *,
        host: str,
    ) -> DatabaseSettings:
        """Read the generated database credential for the selected host."""


def data_source_name(settings: DatabaseSettings) -> str:
    """Return the driver URL for this target, which never carries the password."""
    return f"{DRIVER}://{settings.user}@{settings.host}:{settings.port}/{settings.database}"


def read_credential(deploy: Path) -> str:
    """Return the generated credential, refusing a path the generator has not written.

    Args:
        deploy: The deploy directory ``scripts/broker-secrets.sh`` writes into.

    Returns:
        The credential, stripped of the trailing newline the generator writes.

    Raises:
        SettingsError: With ``MISSING_MATERIAL``, naming the file and the command that
            writes it, so a checkout that never generated its secrets fails at startup
            rather than at connect.
    """
    path = deploy / CREDENTIAL_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SettingsError(SettingsRefusal.MISSING_MATERIAL, str(path)) from error


def database_settings(
    environment: Mapping[str, str],
    deploy: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> DatabaseSettings:
    """Return the durable target the environment and the generated material name.

    Args:
        environment: The process environment, injected so nothing is read at import.
        deploy: The deploy directory holding the generated credential.
        host: Where the cluster answers; ``CONTAINER_HOST`` from inside the network.
        port: The port the cluster answers on.

    Returns:
        The resolved target, with the credential held apart from the data source name.

    Raises:
        SettingsError: With ``MISSING_SETTING``, naming the first setting that is absent or
            blank, or with ``MISSING_MATERIAL`` when the credential was never generated.
    """
    values = []
    for name in (POSTGRES_USER_SETTING, POSTGRES_DB_SETTING):
        value = environment.get(name, "").strip()
        if not value:
            raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
        values.append(value)
    return DatabaseSettings(
        host=host, port=port, user=values[0], database=values[1], password=read_credential(deploy)
    )
