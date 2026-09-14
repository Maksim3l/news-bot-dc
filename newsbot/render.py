"""Turns items into Discord embeds."""

from __future__ import annotations

import zlib

import discord

from newsbot.config import SourceConfig
from newsbot.models import Item

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 500


def source_color(source: SourceConfig) -> discord.Colour:
    if source.color is not None:
        return discord.Colour(source.color)
    # Stable per-source hue so a channel with several sources stays readable.
    hue = zlib.crc32(source.name.encode("utf-8")) % 360
    return discord.Colour.from_hsv(hue / 360, 0.55, 0.95)


def build_embed(item: Item, source: SourceConfig) -> discord.Embed:
    embed = discord.Embed(
        title=item.title[:TITLE_LIMIT] or "(untitled)",
        url=item.url or None,
        description=item.summary[:DESCRIPTION_LIMIT] or None,
        colour=source_color(source),
        timestamp=item.published,
    )
    footer = f"{item.source} - {item.author}" if item.author else item.source
    embed.set_footer(text=footer[:2048])
    if item.image:
        embed.set_thumbnail(url=item.image)
    return embed


def describe_source(source: SourceConfig) -> str:
    state = "on" if source.enabled else "off"
    filters = []
    if source.filters.include:
        filters.append(f"include {len(source.filters.include)}")
    if source.filters.exclude:
        filters.append(f"exclude {len(source.filters.exclude)}")
    suffix = f", {', '.join(filters)}" if filters else ""
    return f"**{source.name}** [{state}] `{source.kind}` every {source.interval}s -> <#{source.channel_id}>{suffix}"
