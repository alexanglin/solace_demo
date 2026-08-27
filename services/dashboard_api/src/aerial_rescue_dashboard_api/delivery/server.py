"""Uvicorn process seam fixed to the private mission-control Unix socket."""

from __future__ import annotations

import os
from typing import Final, Protocol, cast

import uvicorn
from fastapi import FastAPI

DASHBOARD_SOCKET: Final = "/run/aerial-rescue/dashboard-api.sock"
GRACEFUL_SHUTDOWN_SECONDS: Final = 5
SOCKET_UMASK: Final = 0o117


class ServerRunner(Protocol):
    """Injectable Uvicorn-compatible runner used by the process entry point."""

    def __call__(self, application: object, **options: object) -> None:
        """Run one ASGI application with explicit options."""


def run_unix_socket(
    application: FastAPI,
    *,
    runner: ServerRunner | None = None,
    socket_path: str = DASHBOARD_SOCKET,
) -> None:
    """Run only on the configured Unix socket with bounded graceful shutdown."""
    selected = runner or _run_uvicorn
    previous_umask = os.umask(SOCKET_UMASK)
    try:
        selected(
            application,
            uds=socket_path,
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    finally:
        os.umask(previous_umask)


def _run_uvicorn(application: object, **options: object) -> None:
    """Narrow the generic test seam into Uvicorn's explicit typed call."""
    if not isinstance(application, FastAPI):
        message = "dashboard server requires one FastAPI application"
        raise TypeError(message)
    uvicorn.run(
        application,
        uds=cast(str, options["uds"]),
        proxy_headers=cast(bool, options["proxy_headers"]),
        server_header=cast(bool, options["server_header"]),
        access_log=False,
        timeout_graceful_shutdown=cast(int, options["timeout_graceful_shutdown"]),
    )
