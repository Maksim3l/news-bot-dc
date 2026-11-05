"""Entry point.

python -m newsbot           runs the bot
python -m newsbot check     fetches every source and validates digest.yml, no Discord token needed
python -m newsbot digest    fetches every source and prints the digest it would post, posts nothing
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from newsbot.config import ConfigError, Settings, load_settings, load_sources
from newsbot.digest.llm import DigestError
from newsbot.fetchers import get_fetcher
from newsbot.filters import apply_filters
from newsbot.http import FetchError, build_session, download

USAGE = "usage: python -m newsbot [run | check [source name] | digest]"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    # Voice libraries and the message content intent are not used; their startup warnings are noise.
    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("discord.ext.commands.bot").setLevel(logging.ERROR)


def offline_settings() -> Settings:
    settings = load_settings(require_token=False)
    configure_logging(settings.log_level)
    # Nothing is posted offline, so an unset channel must not block validation.
    if settings.default_channel_id is None:
        settings.default_channel_id = 0
    return settings


def describe_digest(settings: Settings) -> str:
    from newsbot.digest.config import load_digest

    if not settings.digest_file.exists():
        return f"digest off ({settings.digest_file} not found)"
    config = load_digest(settings)
    if not config.enabled:
        return "digest off (enabled: false)"
    keys = ", ".join(category.key for category in config.categories)
    return (
        f"digest built {config.build_at:%H:%M}-{config.build_deadline:%H:%M}, posted {config.post_at:%H:%M} "
        f"{config.timezone.key}, {config.provider} model {config.model}, categories: {keys}"
    )


async def check(names: list[str]) -> int:
    """Fetches every source and prints what came back. Needs no Discord token."""
    settings = offline_settings()
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
    print(describe_digest(settings))
    return 1 if failures else 0


async def dry_run_digest() -> int:
    """Fills the pool from every source once, then prints the digest. Posts and records nothing."""
    from newsbot.digest.builder import DigestBuilder
    from newsbot.digest.config import load_digest
    from newsbot.digest.llm import make_model
    from newsbot.digest.pool import CandidatePool
    from newsbot.digest.render import digest_text

    settings = offline_settings()
    config = load_digest(settings)
    sources = [source for source in load_sources(settings) if source.enabled]
    pool = CandidatePool(settings.database_path)
    session = build_session(settings)
    try:
        for source in sources:
            try:
                body = await download(session, source.url, source.headers, source.name)
                items = apply_filters(await get_fetcher(source.kind)(source, body), source.filters)
            except (ConfigError, FetchError) as exc:
                print(f"skip  {source.name}: {exc}")
                continue
            added = pool.add(items, source.language, config.max_age_hours)
            print(f"pool  {source.name}: {len(items)} fetched, {added} new")
        print()
        model = make_model(config, session)
        if hasattr(model, "ensure_model"):
            await model.ensure_model()
        digest = await DigestBuilder(config, pool, model, session).build()
        print(digest_text(digest))
    finally:
        await session.close()
        pool.close()
    return 0


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
        if command == "digest":
            return asyncio.run(dry_run_digest())
        if command in ("run", ""):
            return run()
    except ConfigError as exc:
        print(f"configuration problem: {exc}")
        return 2
    except DigestError as exc:
        print(f"digest failed: {exc}")
        return 1
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
