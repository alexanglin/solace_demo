"""Typed in-memory HTTP and private-auth builders shared by service tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol, cast

import httpx
from fastapi import FastAPI

type AsgiClientFactory = Callable[
    [FastAPI],
    AbstractAsyncContextManager[httpx.AsyncClient],
]


@asynccontextmanager
async def asgi_client(
    application: FastAPI,
    *,
    base_url: str,
    lifespan: bool = True,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield one HTTPX client over an optional in-process application lifespan."""
    async with AsyncExitStack() as stack:
        if lifespan:
            await stack.enter_async_context(application.router.lifespan_context(application))
        transport = httpx.ASGITransport(
            app=application,
            raise_app_exceptions=raise_app_exceptions,
        )
        client = await stack.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url=base_url)
        )
        yield client


def asgi_client_for(
    base_url: str,
    *,
    lifespan: bool = True,
    raise_app_exceptions: bool = True,
) -> AsgiClientFactory:
    """Bind one service origin and lifespan policy to the shared client scope."""

    def configured(application: FastAPI) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return asgi_client(
            application,
            base_url=base_url,
            lifespan=lifespan,
            raise_app_exceptions=raise_app_exceptions,
        )

    return configured


class AsgiExchange(Protocol):
    """One configured non-raising in-process request operation."""

    async def __call__(
        self,
        application: FastAPI,
        method: str,
        path: str,
        *,
        headers: httpx.Headers | Mapping[str, str],
        content: bytes | str | None = None,
    ) -> httpx.Response:
        """Send one request through the configured ASGI origin."""


def asgi_exchange_for(base_url: str) -> AsgiExchange:
    """Bind the origin shared by one service's in-process HTTP tests."""

    async def exchange(
        application: FastAPI,
        method: str,
        path: str,
        *,
        headers: httpx.Headers | Mapping[str, str],
        content: bytes | str | None = None,
    ) -> httpx.Response:
        async with asgi_client(
            application,
            base_url=base_url,
            lifespan=False,
            raise_app_exceptions=False,
        ) as client:
            return await client.request(method, path, headers=headers, content=content)

    return exchange


@dataclass(frozen=True, slots=True)
class PrivateRequestHeaders:
    """Build one service's private Host, bearer, and JSON media headers."""

    host: str
    bearer: str
    title_case: bool = False

    def __call__(
        self,
        *,
        host: str | None = None,
        authorization_value: str | None = None,
        media_type: str = "application/json",
        **changes: str,
    ) -> dict[str, str]:
        """Return fresh headers with explicit test-case overrides applied last."""
        host_name = "Host" if self.title_case else "host"
        authorization_name = "Authorization" if self.title_case else "authorization"
        content_type_name = "Content-Type" if self.title_case else "content-type"
        selected_bearer = self.bearer if authorization_value is None else authorization_value
        headers = {
            host_name: self.host if host is None else host,
            authorization_name: f"Bearer {selected_bearer}",
            content_type_name: media_type,
        }
        headers.update(changes)
        return headers


def error_code(content: bytes) -> str:
    """Return the closed refusal code from one canonical JSON response."""
    return cast("str", json.loads(content)["errorCode"])
