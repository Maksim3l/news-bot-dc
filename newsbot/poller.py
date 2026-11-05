"""Scheduling layer: one independent loop per source, fetch -> filter -> dedupe -> publish."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

from newsbot.config import Settings, SourceConfig
from newsbot.fetchers import get_fetcher
from newsbot.filters import apply_filters
from newsbot.http import FetchError, download
from newsbot.models import Item
from newsbot.store import SeenStore

log = logging.getLogger(__name__)

MAX_BACKOFF = 3600
Publisher = Callable[[SourceConfig, list[Item]], Awaitable[None]]
# Receives every fetched item, posted or not. The daily digest uses it to fill its pool.
Collector = Callable[[SourceConfig, list[Item]], Awaitable[None]]


@dataclass(slots=True)
class SourceStatus:
    last_run: datetime | None = None
    last_error: str | None = None
    posted: int = 0


class Poller:
    def __init__(
        self,
        settings: Settings,
        store: SeenStore,
        session: aiohttp.ClientSession,
        publish: Publisher,
        collect: Collector | None = None,
    ) -> None:
        self._settings = settings
        self._collect = collect
        self._store = store
        self._session = session
        self._publish = publish
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.status: dict[str, SourceStatus] = defaultdict(SourceStatus)

    def start(self, sources: list[SourceConfig]) -> None:
        for source in sources:
            if source.enabled:
                self._tasks[source.name] = asyncio.create_task(
                    self._loop(source), name=f"poll:{source.name}"
                )
        log.info("watching %d sources", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def restart(self, sources: list[SourceConfig]) -> None:
        await self.stop()
        self.start(sources)

    async def fetch_items(self, source: SourceConfig) -> list[Item]:
        """Raw items straight from the site, filtered but not deduped. Used by /preview."""
        body = await download(self._session, source.url, source.headers, source.name)
        items = await get_fetcher(source.kind)(source, body)
        return apply_filters(items, source.filters)

    def record_error(self, source_name: str, message: str) -> None:
        self.status[source_name].last_error = message

    async def poll_once(self, source: SourceConfig) -> list[Item]:
        """Returns the items published this round. Empty on a first run that only seeds the store."""
        items = await self.fetch_items(source)
        state = self.status[source.name]
        state.last_run = datetime.now(timezone.utc)
        state.last_error = None
        if self._collect is not None:
            await self._collect(source, items)
        if not source.post:
            return []
        fresh = await asyncio.to_thread(self._store.new_items, items)
        if not fresh:
            return []

        first_run = not await asyncio.to_thread(self._store.knows_source, source.name)
        if first_run and not self._settings.announce_backlog:
            await asyncio.to_thread(self._store.mark_seen, fresh)
            log.info("%s: seeded %d existing items without posting", source.name, len(fresh))
            return []

        # Oldest first so the channel reads chronologically; anything over the per-poll cap
        # waits for the next tick instead of being dropped.
        fresh.sort(key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc))
        batch = fresh[: source.max_items]
        await self._publish(source, batch)
        await asyncio.to_thread(self._store.mark_seen, batch)
        state.posted += len(batch)
        return batch

    async def _loop(self, source: SourceConfig) -> None:
        # Stagger the first request so twenty sources do not all fire at boot.
        await asyncio.sleep(random.uniform(0, min(30, source.interval)))
        failures = 0
        while True:
            try:
                posted = await self.poll_once(source)
                failures = 0
                if posted:
                    log.info("%s: posted %d item(s)", source.name, len(posted))
            except FetchError as exc:
                failures += 1
                self.record_error(source.name, str(exc))
                log.warning("%s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a broken selector must not kill the other sources
                failures += 1
                self.record_error(source.name, f"{type(exc).__name__}: {exc}")
                log.exception("%s: poll failed", source.name)

            await asyncio.sleep(self._next_delay(source, failures))

    def _next_delay(self, source: SourceConfig, failures: int) -> float:
        if failures:
            return min(source.interval * (2**failures), MAX_BACKOFF)
        return source.interval * random.uniform(0.9, 1.1)
