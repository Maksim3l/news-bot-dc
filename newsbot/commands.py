"""Slash commands. Everything here is a thin wrapper over the config, poller and sources.yml layers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from newsbot import sourcefile
from newsbot.config import ConfigError, SourceConfig
from newsbot.digest.llm import DigestError
from newsbot.digest.render import build_digest_embeds, digest_heading
from newsbot.http import FetchError
from newsbot.render import build_embed, describe_source
from newsbot.subscribe import discover_source

if TYPE_CHECKING:
    from newsbot.bot import NewsBot

log = logging.getLogger(__name__)

EMBEDS_PER_MESSAGE = 10


def setup_commands(bot: "NewsBot") -> None:
    async def complete_source(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        typed = current.lower()
        return [
            app_commands.Choice(name=source.name, value=source.name)
            for source in bot.sources
            if typed in source.name.lower()
        ][:25]

    def require_source(name: str) -> SourceConfig:
        source = bot.find_source(name)
        if source is None:
            raise ConfigError(f"no source named {name!r}. Use /sources to see the list.")
        return source

    @app_commands.command(name="sources", description="List every site the bot is watching")
    async def sources_command(interaction: discord.Interaction) -> None:
        if not bot.sources:
            await interaction.response.send_message("No sources configured yet. Use /watch.", ephemeral=True)
            return
        lines = [describe_source(source) for source in bot.sources]
        await interaction.response.send_message(_paginate(lines), ephemeral=True)

    @app_commands.command(name="status", description="Show polling health per source")
    async def status_command(interaction: discord.Interaction) -> None:
        poller = bot.poller
        lines = []
        for source in bot.sources:
            state = poller.status[source.name] if poller else None
            last_run = f"<t:{int(state.last_run.timestamp())}:R>" if state and state.last_run else "never"
            posted = state.posted if state else 0
            problem = f", last error: {state.last_error}" if state and state.last_error else ""
            lines.append(f"**{source.name}** last run {last_run}, posted {posted}{problem}")
        remembered = bot.store.count() if bot.store else 0
        lines.append(f"\n{remembered} items remembered in the dedupe store.")
        await interaction.response.send_message(_paginate(lines), ephemeral=True)

    @app_commands.command(name="preview", description="Show the newest items from a source without posting them")
    @app_commands.describe(name="Source name", count="How many items to show (1-10)")
    @app_commands.autocomplete(name=complete_source)
    async def preview_command(interaction: discord.Interaction, name: str, count: int = 3) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            source = require_source(name)
            items = await bot.poller.fetch_items(source)
        except (ConfigError, FetchError) as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send(f"{source.name} returned no items. Check the selectors.", ephemeral=True)
            return

        embeds = [build_embed(item, source) for item in items[: max(1, min(count, EMBEDS_PER_MESSAGE))]]
        await interaction.followup.send(
            f"{len(items)} item(s) available from **{source.name}**:", embeds=embeds, ephemeral=True
        )

    @app_commands.command(name="watch", description="Watch a new site (finds its feed automatically)")
    @app_commands.describe(
        url="Homepage or feed URL",
        name="Optional display name",
        channel="Channel to post in (defaults to DISCORD_CHANNEL_ID)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def watch_command(
        interaction: discord.Interaction,
        url: str,
        name: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            found = await discover_source(bot.http_session, url, name)
        except (ConfigError, FetchError) as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        entry: dict[str, object] = {"name": found.name, "url": found.url, "type": found.kind}
        if channel is not None:
            entry["channel"] = channel.id
        try:
            sourcefile.append_source(bot.settings.sources_file, entry)
        except ConfigError as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        try:
            await bot.reload_sources()
            source = require_source(found.name)
            items = await bot.poller.fetch_items(source)
        except (ConfigError, FetchError) as exc:
            # A rejected entry left in the file would stop the bot from booting next time.
            sourcefile.remove_source(bot.settings.sources_file, found.name)
            await bot.reload_sources()
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        target = channel.mention if channel else f"<#{source.channel_id}>"
        newest = f"\nNewest: {items[0].title}" if items else ""
        await interaction.followup.send(
            f"Watching **{found.name}** (`{found.url}`) in {target}, every {source.interval}s. "
            f"{len(items)} item(s) found; only items published from now on get posted.{newest}",
            ephemeral=True,
        )

    @app_commands.command(name="unwatch", description="Stop watching a site")
    @app_commands.describe(name="Source name", forget="Also clear its dedupe history")
    @app_commands.autocomplete(name=complete_source)
    @app_commands.default_permissions(manage_guild=True)
    async def unwatch_command(interaction: discord.Interaction, name: str, forget: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        source = bot.find_source(name)
        if source is None or not sourcefile.remove_source(bot.settings.sources_file, source.name):
            await interaction.followup.send(f"Error: no source named {name!r}", ephemeral=True)
            return
        removed = bot.store.forget_source(source.name) if forget and bot.store else 0
        await bot.reload_sources()
        suffix = f", forgot {removed} remembered item(s)" if forget else ""
        await interaction.followup.send(f"Stopped watching **{source.name}**{suffix}.", ephemeral=True)

    @app_commands.command(name="pause", description="Pause a source without deleting it")
    @app_commands.autocomplete(name=complete_source)
    @app_commands.default_permissions(manage_guild=True)
    async def pause_command(interaction: discord.Interaction, name: str) -> None:
        await _toggle(interaction, name, enabled=False)

    @app_commands.command(name="resume", description="Resume a paused source")
    @app_commands.autocomplete(name=complete_source)
    @app_commands.default_permissions(manage_guild=True)
    async def resume_command(interaction: discord.Interaction, name: str) -> None:
        await _toggle(interaction, name, enabled=True)

    async def _toggle(interaction: discord.Interaction, name: str, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        source = bot.find_source(name)
        if source is None or not sourcefile.set_enabled(bot.settings.sources_file, source.name, enabled):
            await interaction.followup.send(f"Error: no source named {name!r}", ephemeral=True)
            return
        await bot.reload_sources()
        word = "Resumed" if enabled else "Paused"
        await interaction.followup.send(f"{word} **{source.name}**.", ephemeral=True)

    @app_commands.command(name="pollnow", description="Poll one source or all of them right now")
    @app_commands.describe(name="Leave empty to poll every enabled source")
    @app_commands.autocomplete(name=complete_source)
    @app_commands.default_permissions(manage_guild=True)
    async def pollnow_command(interaction: discord.Interaction, name: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            targets = [require_source(name)] if name else bot.enabled_sources
        except ConfigError as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        results = []
        for source in targets:
            try:
                posted = await bot.poller.poll_once(source)
                results.append(f"**{source.name}**: {len(posted)} posted")
            except (ConfigError, FetchError) as exc:
                bot.poller.record_error(source.name, str(exc))
                results.append(f"**{source.name}**: error, {exc}")
        await interaction.followup.send(_paginate(results), ephemeral=True)

    @app_commands.command(name="reload", description="Re-read sources.yml from disk")
    @app_commands.default_permissions(manage_guild=True)
    async def reload_command(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            sources = await bot.reload_sources()
        except ConfigError as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Reloaded {len(sources)} source(s), {len(bot.enabled_sources)} active.", ephemeral=True
        )

    @app_commands.command(name="digest", description="Show or post today's digest")
    @app_commands.describe(
        post="Post it to the digest channel (default: only show it to you)",
        rebuild="Run the model again even if today's digest is already built (takes minutes on CPU)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def digest_command(interaction: discord.Interaction, post: bool = False, rebuild: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            digest = None if rebuild else await bot.stored_digest()
            if digest is None:
                digest = await bot.build_digest()
        except (ConfigError, DigestError) as exc:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
            return

        if post:
            sent = await bot.post_digest(digest)
            word = "Posted" if sent else "Nothing posted for"
            await interaction.followup.send(
                f"{word} today's digest ({len(digest.entries)} pick(s) from {digest.considered} articles).",
                ephemeral=True,
            )
            return

        embeds = build_digest_embeds(digest)[:EMBEDS_PER_MESSAGE]
        summary = f"{digest_heading(digest)} preview, {digest.considered} articles considered."
        if digest.empty:
            summary += "\nNothing cleared the bar."
        await interaction.followup.send(summary, embeds=embeds, ephemeral=True)

    for command in (
        digest_command,
        sources_command,
        status_command,
        preview_command,
        watch_command,
        unwatch_command,
        pause_command,
        resume_command,
        pollnow_command,
        reload_command,
    ):
        bot.tree.add_command(command)


def _paginate(lines: list[str], limit: int = 1900) -> str:
    """Discord rejects messages over 2000 characters, so long source lists get cut with a count."""
    text = ""
    for index, line in enumerate(lines):
        if len(text) + len(line) + 1 > limit:
            return f"{text}\n… and {len(lines) - index} more"
        text += line + "\n"
    return text.strip() or "(nothing)"
