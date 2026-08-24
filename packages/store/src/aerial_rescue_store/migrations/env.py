"""The Alembic environment, written by hand because the generated one does not type-check.

The stock template passes ``config.config_file_name``, typed ``str | None``, into ``fileConfig``,
which takes a ``str``. This repository forbids the inline suppression that would silence it
(``docs/adr/0011``) and contains no such suppression anywhere, so the file is written rather than
generated (``docs/adr/0087``).

There is no branch here. Whether this run applies revisions to a supplied connection or renders
them as statements is decided by ``aerial_rescue_store.migration.environment_arguments``, which is
an ordinary function with ordinary tests -- a file Alembic executes by path is the worst place to
put a decision, because covering it needs a migration run.

There is no autogenerate target either. Revisions here are written and reviewed, never diffed from
model metadata, so a target would be a second description of the schema that could disagree with
the one that runs.
"""

from __future__ import annotations

from aerial_rescue_store.migration import (
    CONNECTION_ATTRIBUTE,
    URL_OPTION,
    environment_arguments,
)
from alembic import context
from sqlalchemy import MetaData

TARGET_METADATA: MetaData | None = None
"""No autogenerate target: revisions are hand-written and reviewed (``docs/adr/0087``)."""


def run() -> None:
    """Configure the environment for this run and apply the revisions it asks for."""
    arguments = environment_arguments(
        context.config.attributes.get(CONNECTION_ATTRIBUTE),
        context.config.get_main_option(URL_OPTION),
    )
    context.configure(
        connection=arguments.connection,
        url=arguments.url,
        target_metadata=TARGET_METADATA,
        as_sql=arguments.as_sql,
        literal_binds=arguments.literal_binds,
        dialect_opts=dict(arguments.dialect_opts),
    )
    with context.begin_transaction():
        context.run_migrations()


run()
