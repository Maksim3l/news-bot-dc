"""Fires the digest once a day at post_at in the configured time zone."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from newsbot.digest.config import DigestConfig

log = logging.getLogger(__name__)

RETRY_SECONDS = 600
ATTEMPTS = 3


def seconds_until(config: DigestConfig, now: datetime | None = None) -> float:
    now = now or datetime.now(config.timezone)
    target = now.replace(hour=config.post_at.hour, minute=config.post_at.minute, second=0, microsecond=0)
    if target <= now:
        target = (target + timedelta(days=1)).replace(tzinfo=config.timezone)
    # Subtracting in UTC: same-zone subtraction ignores a DST change in between and lands an hour off.
    return (target.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()


class DailyScheduler:
    def __init__(self, config: DigestConfig, run: Callable[[], Awaitable[None]]) -> None:
        self._config = config
        self._run = run
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="digest")
        log.info("digest scheduled daily at %s %s", self._config.post_at.strftime("%H:%M"), self._config.timezone.key)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(seconds_until(self._config))
            # A failed run (API down, network) retries a few times before giving up on the day.
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    await self._run()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("digest run failed (attempt %d of %d)", attempt, ATTEMPTS)
                    if attempt < ATTEMPTS:
                        await asyncio.sleep(RETRY_SECONDS)
