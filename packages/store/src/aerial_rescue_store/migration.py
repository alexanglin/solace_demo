"""How the schema history is located, configured, and rendered without a database.

ADR-0087 (``docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md``)
places the Alembic tree inside this package so its revisions are attributed to this member's Tier 2
coverage, ship in the wheel, and are governed by this member's guide. It also decides how that
coverage is earned. Alembic's offline mode executes a revision's ``upgrade`` and ``downgrade``
bodies against a statement-emitting context and never opens a connection, so a member-local test
runs the real bodies rather than describing them.

There is no ``alembic.ini``. The configuration is built here, from the package's own location, so a
caller cannot point the runner at a different history by changing a file on disk, and an installed
wheel finds its own revisions.

This module opens nothing. It renders statements, and it configures the live run that applies
them -- but the connection that run applies through is supplied by the caller, so the decision of
*how* a run is configured stays here with ordinary tests while the connection stays outside.
"""

from __future__ import annotations

import contextlib
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import Connection

SCRIPT_DIRECTORY: Final = Path(__file__).parent / "migrations"
"""The Alembic tree, resolved from this package rather than from the working directory."""

VERSION_SERIES: Final = ("v1",)
"""Release series under ``versions/``, sharded before the fan-out cap can be reached (ADR-0033)."""

URL_OPTION: Final = "sqlalchemy.url"

PATH_SEPARATOR_OPTION: Final = "path_separator"
PATH_SEPARATOR: Final = "os"
"""Alembic 1.19 deprecates its legacy space-and-comma splitting of ``version_locations``."""
CONNECTION_ATTRIBUTE: Final = "connection"
"""Where a live runner puts its connection so ``env.py`` uses it instead of rendering statements."""

PARAMSTYLE_OPTION: Final = "paramstyle"
PARAMSTYLE: Final = "named"

LIVE_URL: Final = ""
"""A live run reaches its database through the connection, so ``env.py`` reads no URL."""

AUDIT_SEQUENCE_TABLE: Final = "audit_sequence"
AUDIT_RECORD_TABLE: Final = "audit_record"
APPROVAL_TABLE: Final = "approval"
APPROVAL_BINDING_TABLE: Final = "operator_decision_binding"
IDEMPOTENCY_CLAIM_TABLE: Final = "idempotency_claim"
COMMAND_OUTBOX_TABLE: Final = "command_outbox"
BROKER_INBOX_TABLE: Final = "broker_inbox"
BROKER_REFUSAL_TABLE: Final = "broker_refusal"
SOURCE_EVENT_TABLE: Final = "source_event"
PENDING_INVOCATION_TABLE: Final = "pending_invocation"
SOURCE_EVIDENCE_ITEM_TABLE: Final = "source_evidence_item"
APPLICATION_OUTBOX_TABLE: Final = "application_outbox"
PROPOSAL_TABLE: Final = "proposal"
EVIDENCE_ITEM_TABLE: Final = "evidence_item"
EVIDENCE_DECISION_TABLE: Final = "evidence_decision"
COMMAND_PROGRESS_TABLE: Final = "command_progress"
DRONE_COMMAND_RECEIPT_TABLE: Final = "drone_command_receipt"
DRONE_STREAM_STATE_TABLE: Final = "drone_stream_state"
DRONE_COMMAND_EFFECT_TABLE: Final = "drone_command_effect"

BASE_REVISION: Final = "base"
HEAD_REVISION: Final = "head"


def version_locations() -> tuple[Path, ...]:
    """Return every release series Alembic should read revisions from."""
    return tuple(SCRIPT_DIRECTORY / "versions" / series for series in VERSION_SERIES)


def migration_config(url: str) -> Config:
    """Return the configuration for this package's own history, addressed at ``url``."""
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_DIRECTORY))
    config.set_main_option(PATH_SEPARATOR_OPTION, PATH_SEPARATOR)
    config.set_main_option(
        "version_locations", os.pathsep.join(str(path) for path in version_locations())
    )
    config.set_main_option(URL_OPTION, url)
    return config


def live_config(connection: Connection) -> Config:
    """Return a configuration that applies this package's history through ``connection``.

    Alembic reaches a live connection through ``config.attributes`` rather than through an
    option, because a connection is an object and the option table holds strings. Putting that
    one assignment here rather than in a caller keeps ``env.py`` branchless and keeps the live
    and rendering configurations built by the same function, so they cannot drift apart in
    which history they read.

    Args:
        connection: The open connection the revisions are applied through. Its transaction,
            its lifetime, and the database it addresses all belong to the caller.

    Returns:
        The configuration. Building it opens nothing; the caller's connection is already open.
    """
    config = migration_config(LIVE_URL)
    config.attributes[CONNECTION_ATTRIBUTE] = connection
    return config


@dataclass(frozen=True)
class EnvironmentArguments:
    """How the environment configures Alembic, decided here so ``env.py`` carries no branch.

    A connection means a live run and everything else is inert; no connection means the run
    renders statements instead. Keeping the choice in this module rather than in ``env.py`` is
    what lets both halves be proven without a database (``docs/adr/0087``).
    """

    connection: Connection | None
    url: str | None
    as_sql: bool
    literal_binds: bool
    dialect_opts: Mapping[str, str]


def environment_arguments(connection: Connection | None, url: str | None) -> EnvironmentArguments:
    """Return how to configure the environment for a live connection, or for rendering."""
    if connection is None:
        return EnvironmentArguments(
            connection=None,
            url=url,
            as_sql=True,
            literal_binds=True,
            dialect_opts={PARAMSTYLE_OPTION: PARAMSTYLE},
        )
    return EnvironmentArguments(
        connection=connection, url=None, as_sql=False, literal_binds=False, dialect_opts={}
    )


def heads(config: Config) -> tuple[str, ...]:
    """Return every head revision, which must be exactly one for a linear history."""
    return tuple(ScriptDirectory.from_config(config).get_heads())


def revisions(config: Config) -> tuple[str, ...]:
    """Return every revision identifier, newest first."""
    directory = ScriptDirectory.from_config(config)
    return tuple(script.revision for script in directory.walk_revisions())


def upgrade_statements(config: Config, revision: str = HEAD_REVISION) -> str:
    """Return the data definition an upgrade to ``revision`` would issue, without connecting."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        command.upgrade(config, revision, sql=True)
    return captured.getvalue()


def downgrade_statements(config: Config, revision: str = BASE_REVISION) -> str:
    """Return the data definition a downgrade to ``revision`` would issue, without connecting."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        command.downgrade(config, f"{HEAD_REVISION}:{revision}", sql=True)
    return captured.getvalue()
