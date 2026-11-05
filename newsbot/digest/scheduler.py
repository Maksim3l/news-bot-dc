"""Daily timing: build between build_at and build_deadline, post the stored digest at post_at.

Building is the only step that uses the model, so the machine is busy only inside the build window.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from newsbot.digest.config import DigestConfig

log = logging.getLogger(__name__)

RETRY_SECONDS = 600


@dataclass(slots=True)
class DayState:
    built: bool
    posted: bool


@dataclass(slots=True)
class Step:
    action: str  # "wait", "build" or "post"
    seconds: float = 0.0


def at(config: DigestConfig, day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=config.timezone)


def between(start: datetime, end: datetime) -> float:
    # In UTC: same-zone subtraction ignores a DST change in between and lands an hour off.
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()


def next_step(config: DigestConfig, now: datetime, state: DayState) -> Step:
    today = now.date()
    build_at = at(config, today, config.build_at)
    deadline = at(config, today, config.build_deadline)
    post_at = at(config, today, config.post_at)
    tomorrow_build = at(config, today + timedelta(days=1), config.build_at)

    if state.posted:
        return Step("wait", between(now, tomorrow_build))
    if not state.built:
        if now < build_at:
            return Step("wait", between(now, build_at))
        if now < deadline:
            return Step("build")
        # Missed the window (bot was off, or the build failed every time): skip the day rather than
        # loading the model during working hours.
        return Step("wait", between(now, tomorrow_build))
    if now < post_at:
        return Step("wait", between(now, post_at))
    return Step("post")


class DailyScheduler:
    def __init__(
        self,
        config: DigestConfig,
        state: Callable[[date], Awaitable[DayState]],
        build: Callable[[], Awaitable[None]],
        post: Callable[[], Awaitable[None]],
    ) -> None:
        self._config = config
        self._state = state
        self._build = build
        self._post = post
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="digest")
        c = self._config
        log.info(
            "digest: build %s-%s, post %s (%s)",
            f"{c.build_at:%H:%M}", f"{c.build_deadline:%H:%M}", f"{c.post_at:%H:%M}", c.timezone.key,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            now = datetime.now(self._config.timezone)
            step = next_step(self._config, now, await self._state(now.date()))
            if step.action == "wait":
                await asyncio.sleep(max(step.seconds, 1))
            elif step.action == "build":
                await self._run_build(now)
            else:
                await self._run_post()

    async def _run_build(self, now: datetime) -> None:
        budget = between(now, at(self._config, now.date(), self._config.build_deadline))
        try:
            await asyncio.wait_for(self._build(), timeout=budget)
        except TimeoutError:
            log.error("digest build was still running at %s and was stopped; no digest today",
                      f"{self._config.build_deadline:%H:%M}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("digest build failed")
            await asyncio.sleep(min(RETRY_SECONDS, max(budget, 1)))

    async def _run_post(self) -> None:
        try:
            await self._post()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("digest post failed, retrying in %d s", RETRY_SECONDS)
            await asyncio.sleep(RETRY_SECONDS)
