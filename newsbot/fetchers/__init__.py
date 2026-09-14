"""Fetcher registry. Adding a new kind of site means adding a module here and registering it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from newsbot.config import ConfigError, SourceConfig
from newsbot.models import Item

Fetcher = Callable[..., Awaitable[list[Item]]]

_REGISTRY: dict[str, Fetcher] = {}


def register(kind: str) -> Callable[[Fetcher], Fetcher]:
    def decorate(func: Fetcher) -> Fetcher:
        _REGISTRY[kind] = func
        return func

    return decorate


def get_fetcher(kind: str) -> Fetcher:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise ConfigError(f"unknown source type {kind!r}; available types: {', '.join(sorted(_REGISTRY))}") from None


def available_kinds() -> list[str]:
    return sorted(_REGISTRY)


from newsbot.fetchers import html, json_api, rss  # noqa: E402,F401  (import registers the fetchers)

__all__ = ["Fetcher", "SourceConfig", "available_kinds", "get_fetcher", "register"]
