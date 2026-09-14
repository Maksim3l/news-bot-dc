"""Entry point. `python -m newsbot` runs the bot, `python -m newsbot check` validates sources offline."""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from newsbot.config import ConfigError, load_settings, load_sources
from newsbot.fetchers import get_fetcher
from newsbot.filters import apply_filters
from newsbot.http import FetchError, build_session, download

USAGE = "usage: python -m newsbot [run|check [source name]]"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)


async def check(names: list[str]) -> int:
    """Fetches every source and prints what came back. Needs no Discord token."""
    settings = load_settings(require_token=False)
    configure_logging(settings.log_level)
    # Nothing is posted in check mode, so an unset channel must not block validation.
    if settings.default_channel_id is None:
        settings.default_channel_id = 0
    sources = load_sources(settings)
    if names:
        wanted = {name.lower() for name in names}
        sources = [source for source in sources if source.name.lower() in wanted]
        if not sources:
            print(f"no source matches {', '.join(names)}")
            return 1

    failures = 0
    session = build_session(settings)
    try:
        for source in sources:
            try:
                body = await download(session, source.url, source.headers, source.name)
                items = apply_filters(await get_fetcher(source.kind)(source, body), source.filters)
            except (ConfigError, FetchError) as exc:
                failures += 1
                print(f"FAIL  {source.name}: {exc}")
                continue
            if not items:
                failures += 1
                print(f"EMPTY {source.name}: fetched fine but produced no items")
                continue
            print(f"OK    {source.name}: {len(items)} item(s) | newest: {items[0].title}")
    finally:
        await session.close()

    print(f"\n{len(sources) - failures}/{len(sources)} sources healthy")
    return 1 if failures else 0


def run() -> int:
    from newsbot.bot import NewsBot

    settings = load_settings()
    configure_logging(settings.log_level)
    bot = NewsBot(settings)
    try:
        bot.run(settings.token, log_handler=None)
    except discord.LoginFailure:
        print("Discord rejected the token. Check DISCORD_TOKEN in .env.")
        return 1
    except discord.PrivilegedIntentsRequired:
        print("Discord asked for privileged intents; this bot needs none, so re-invite it with a clean scope.")
        return 1
    return 0


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    try:
        if command == "check":
            return asyncio.run(check(argv[1:]))
        if command in ("run", ""):
            return run()
    except ConfigError as exc:
        print(f"configuration problem: {exc}")
        return 2
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
