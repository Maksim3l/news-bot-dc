"""Discord layer: owns the client, publishes items, reloads configuration on demand."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from newsbot.commands import setup_commands
from newsbot.config import Settings, SourceConfig, load_sources
from newsbot.http import build_session
from newsbot.models import Item
from newsbot.poller import Poller
from newsbot.render import build_embed
from newsbot.store import SeenStore

log = logging.getLogger(__name__)

SEND_GAP_SECONDS = 1.0


class NewsBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # Only default intents: no privileged intent has to be enabled in the developer portal.
        super().__init__(command_prefix="!newsbot ", intents=discord.Intents.default(), help_command=None)
        self.settings = settings
        self.sources: list[SourceConfig] = []
        self.store: SeenStore | None = None
        self.poller: Poller | None = None
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.sources = load_sources(self.settings)
        self.store = SeenStore(self.settings.database_path)
        self.store.prune(self.settings.retention_days)
        self.http_session = build_session(self.settings)
        self.poller = Poller(self.settings, self.store, self.http_session, self.publish)

        setup_commands(self)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        self.poller.start(self.sources)

    async def on_ready(self) -> None:
        log.info("logged in as %s, watching %d sources", self.user, len(self.enabled_sources))
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=f"{len(self.enabled_sources)} sites"
        ))

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [source for source in self.sources if source.enabled]

    def find_source(self, name: str) -> SourceConfig | None:
        lowered = name.strip().lower()
        return next((source for source in self.sources if source.name.lower() == lowered), None)

    async def publish(self, source: SourceConfig, items: list[Item]) -> None:
        channel = await self._resolve_channel(source)
        if channel is None:
            return
        for item in items:
            try:
                await channel.send(embed=build_embed(item, source))
            except discord.HTTPException as exc:
                log.warning("%s: could not post %s (%s)", source.name, item.url, exc)
            await asyncio.sleep(SEND_GAP_SECONDS)

    async def reload_sources(self) -> list[SourceConfig]:
        self.sources = load_sources(self.settings)
        if self.poller:
            await self.poller.restart(self.enabled_sources)
        return self.sources

    async def close(self) -> None:
        if self.poller:
            await self.poller.stop()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        if self.store:
            self.store.close()
        await super().close()

    async def _resolve_channel(self, source: SourceConfig) -> discord.abc.Messageable | None:
        channel = self.get_channel(source.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(source.channel_id)
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("%s: channel %s unreachable (%s)", source.name, source.channel_id, exc)
                return None
        if not isinstance(channel, discord.abc.Messageable):
            log.error("%s: channel %s cannot receive messages", source.name, source.channel_id)
            return None
        return channel
