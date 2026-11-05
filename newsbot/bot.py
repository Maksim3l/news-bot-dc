"""Discord layer: owns the client, publishes items, reloads configuration on demand."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from newsbot.commands import setup_commands
from newsbot.config import ConfigError, Settings, SourceConfig, load_sources
from newsbot.digest.builder import Digest, DigestBuilder
from newsbot.digest.config import DigestConfig, load_digest
from newsbot.digest.llm import ClaudeJsonModel
from newsbot.digest.pool import CandidatePool
from newsbot.digest.render import build_digest_embeds, digest_heading
from newsbot.digest.scheduler import DailyScheduler
from newsbot.http import build_session
from newsbot.models import Item
from newsbot.poller import Poller
from newsbot.render import build_embed
from newsbot.store import SeenStore

log = logging.getLogger(__name__)

SEND_GAP_SECONDS = 1.0
EMBEDS_PER_MESSAGE = 10
INVITE_PERMISSIONS = discord.Permissions(send_messages=True, embed_links=True)


class NewsBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # Only default intents: no privileged intent has to be enabled in the developer portal.
        super().__init__(
            command_prefix="!newsbot ",
            intents=discord.Intents.default(),
            help_command=None,
            application_id=settings.application_id,
        )
        self.settings = settings
        self.sources: list[SourceConfig] = []
        self.store: SeenStore | None = None
        self.poller: Poller | None = None
        self.http_session: aiohttp.ClientSession | None = None
        self.digest_config: DigestConfig | None = None
        self.pool: CandidatePool | None = None
        self.digest_builder: DigestBuilder | None = None
        self.scheduler: DailyScheduler | None = None

    async def setup_hook(self) -> None:
        self.sources = load_sources(self.settings)
        self.store = SeenStore(self.settings.database_path)
        self.store.prune(self.settings.retention_days)
        self.http_session = build_session(self.settings)
        self.pool = CandidatePool(self.settings.database_path)
        self._load_digest()
        self.poller = Poller(self.settings, self.store, self.http_session, self.publish, self.collect)

        setup_commands(self)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        self.poller.start(self.sources)
        self._start_scheduler()

    async def on_ready(self) -> None:
        log.info("logged in as %s, watching %d sources", self.user, len(self.enabled_sources))
        if self.application_id:
            invite = discord.utils.oauth_url(
                self.application_id, permissions=INVITE_PERMISSIONS, scopes=("bot", "applications.commands")
            )
            log.info("invite link: %s", invite)
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
        if self.scheduler:
            await self.scheduler.stop()
        self._load_digest()
        self._start_scheduler()
        if self.poller:
            await self.poller.restart(self.enabled_sources)
        return self.sources

    @property
    def digest_enabled(self) -> bool:
        return self.digest_builder is not None

    async def collect(self, source: SourceConfig, items: list[Item]) -> None:
        if self.pool is None or self.digest_config is None:
            return
        await asyncio.to_thread(self.pool.add, items, source.language, self.digest_config.max_age_hours)

    async def build_digest(self) -> Digest:
        if self.digest_builder is None:
            raise ConfigError(f"the digest is off: {self.settings.digest_file} is missing or has enabled: false")
        return await self.digest_builder.build()

    async def post_digest(self, digest: Digest) -> bool:
        """Sends the digest and remembers its picks. False when nothing was sent."""
        builder, config = self.digest_builder, self.digest_config
        if builder is None or config is None:
            return False
        if digest.empty and not config.post_when_empty:
            log.info("digest %s: nothing cleared the bar, not posting", digest.day)
            await asyncio.to_thread(builder.mark_posted, digest)
            return False
        channel = await self._channel(config.channel_id, "digest")
        if channel is None:
            return False
        embeds = build_digest_embeds(digest)
        content = digest_heading(digest) if embeds else f"{digest_heading(digest)}\nNothing cleared the bar today."
        for start in range(0, max(len(embeds), 1), EMBEDS_PER_MESSAGE):
            await channel.send(content=content if start == 0 else None, embeds=embeds[start : start + EMBEDS_PER_MESSAGE])
        await asyncio.to_thread(builder.mark_posted, digest)
        return True

    async def run_daily_digest(self) -> None:
        builder = self.digest_builder
        if builder is None or self.pool is None:
            return
        day = builder.today().isoformat()
        if await asyncio.to_thread(self.pool.has_run, day):
            log.info("digest %s already posted, skipping", day)
            return
        digest = await self.build_digest()
        await self.post_digest(digest)
        await asyncio.to_thread(self.pool.prune, self.digest_config.keep_days)

    def _load_digest(self) -> None:
        self.digest_config, self.digest_builder = None, None
        if not self.settings.digest_file.exists():
            log.info("%s not found, daily digest is off", self.settings.digest_file)
            return
        config = load_digest(self.settings)
        if not config.enabled:
            log.info("daily digest is disabled in %s", self.settings.digest_file)
            return
        self.digest_config = config
        self.digest_builder = DigestBuilder(config, self.pool, ClaudeJsonModel(config), self.http_session)

    def _start_scheduler(self) -> None:
        self.scheduler = None
        if self.digest_config is not None:
            self.scheduler = DailyScheduler(self.digest_config, self.run_daily_digest)
            self.scheduler.start()

    async def close(self) -> None:
        if self.scheduler:
            await self.scheduler.stop()
        if self.poller:
            await self.poller.stop()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        if self.store:
            self.store.close()
        if self.pool:
            self.pool.close()
        await super().close()

    async def _resolve_channel(self, source: SourceConfig) -> discord.abc.Messageable | None:
        return await self._channel(source.channel_id, source.name)

    async def _channel(self, channel_id: int, owner: str) -> discord.abc.Messageable | None:
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("%s: channel %s unreachable (%s)", owner, channel_id, exc)
                return None
        if not isinstance(channel, discord.abc.Messageable):
            log.error("%s: channel %s cannot receive messages", owner, channel_id)
            return None
        return channel
