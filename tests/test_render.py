from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import discord

from newsbot.config import Settings, SourceConfig, load_sources
from newsbot.models import Item
from newsbot.render import build_embed, describe_source, source_color

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def test_embed_carries_the_item(rss_bytes: bytes) -> None:
    source = SourceConfig(name="Slo-Tech", url="https://slo-tech.com/rss", channel_id=7, color=0x2B7FFF)
    item = Item(
        source="Slo-Tech",
        title="Nova graficna kartica",
        url="https://slo-tech.com/novice/t111",
        summary="Kratek opis.",
        author="Matej",
        image="https://slo-tech.com/img.png",
        published=datetime(2025, 9, 9, 8, 0, tzinfo=timezone.utc),
    )
    embed = build_embed(item, source)

    assert embed.title == item.title
    assert embed.url == item.url
    assert embed.description == item.summary
    assert embed.colour == discord.Colour(0x2B7FFF)
    assert embed.footer.text == "Slo-Tech - Matej"
    assert embed.thumbnail.url == item.image


def test_long_fields_are_trimmed_to_discord_limits() -> None:
    source = SourceConfig(name="Long", url="https://long.example/rss")
    item = Item(source="Long", title="t" * 400, url="https://long.example/1", summary="s" * 900)
    embed = build_embed(item, source)

    assert len(embed.title) <= 256
    assert len(embed.description) <= 500


def test_colour_is_stable_per_source_when_unset() -> None:
    first = SourceConfig(name="Phoronix", url="u")
    again = SourceConfig(name="Phoronix", url="u")
    other = SourceConfig(name="Hackaday", url="u")

    assert source_color(first) == source_color(again)
    assert source_color(first) != source_color(other)


def test_describe_source_reads_as_one_line() -> None:
    source = SourceConfig(name="Slo-Tech", url="u", channel_id=42, interval=300, enabled=False)
    assert describe_source(source) == "**Slo-Tech** [off] `rss` every 300s -> <#42>"


def test_the_shipped_sources_file_parses(settings: Settings) -> None:
    settings.sources_file = REPOSITORY_ROOT / "sources.yml"
    sources = load_sources(settings)

    assert any(source.name == "Slo-Tech" for source in sources)
    assert all(source.url.startswith("http") for source in sources)
