"""Assemble the Platform service's database URL inside the container (docs/adr/0222).

Every other store consumer in this repository takes a user in its URL and supplies the password
separately, so no credential is ever written down. The Agent Mesh Platform service takes one
``database_url`` string with the credential inside it, and there is nowhere safe to put that
string: the compose policy gate refuses a URL carrying userinfo, the configuration validator
holds ``database_url`` to whole environment indirection, and this repository is public.

So it is assembled here, at the one point that is already inside the container and already holds
the mounted secret, and exported before any configuration is read. A missing part is a refusal
naming the variable rather than a partial URL, because the alternative is a process that starts
and then dies inside ``make_url`` with the value in the traceback.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from urllib.parse import quote

DATABASE_URL_VARIABLE = "PLATFORM_DATABASE_URL"
USER_VARIABLE = "POSTGRES_USER"
HOST_VARIABLE = "PLATFORM_DATABASE_HOST"
PORT_VARIABLE = "PLATFORM_DATABASE_PORT"
NAME_VARIABLE = "PLATFORM_DATABASE_NAME"
CREDENTIAL_FILE_VARIABLE = "PLATFORM_DATABASE_PASSWORD_FILE"
DRIVER = "postgresql+psycopg2"


class PlatformDatabaseError(RuntimeError):
    """A Platform database setting is present but unusable."""

    def __init__(self, variable: str) -> None:
        """Create a failure naming only the variable, never the value it carried."""
        super().__init__(f"Platform database configuration is unusable: {variable}")


def _required(values: Mapping[str, str], variable: str) -> str:
    """Return one non-blank setting, or refuse by naming the variable that is empty."""
    value = values.get(variable, "").strip()
    if not value:
        raise PlatformDatabaseError(variable)
    return value


def _password(values: Mapping[str, str]) -> str:
    """Read the mounted secret, refusing an unreadable or empty file by its variable name."""
    path = Path(_required(values, CREDENTIAL_FILE_VARIABLE))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlatformDatabaseError(CREDENTIAL_FILE_VARIABLE) from error
    password = content.strip()
    if not password:
        raise PlatformDatabaseError(CREDENTIAL_FILE_VARIABLE)
    return password


def export_platform_database_url(environment: MutableMapping[str, str]) -> bool:
    """Set the Platform service's database URL, and report whether this call set it.

    Left alone when a URL was supplied explicitly, and skipped entirely when no database name is
    configured, which is how a deployment that does not run the Platform service starts normally.
    """
    if environment.get(DATABASE_URL_VARIABLE, "").strip():
        return False
    if not environment.get(NAME_VARIABLE, "").strip():
        return False
    user = _required(environment, USER_VARIABLE)
    host = _required(environment, HOST_VARIABLE)
    port = _required(environment, PORT_VARIABLE)
    database = _required(environment, NAME_VARIABLE)
    credential = f"{quote(user, safe='')}:{quote(_password(environment), safe='')}"
    environment[DATABASE_URL_VARIABLE] = f"{DRIVER}://{credential}@{host}:{port}/{database}"
    return True
