from __future__ import annotations

import pytest

from newsbot.config import ConfigError, Settings, load_sources, parse_color


def write_sources(settings: Settings, body: str) -> None:
    settings.sources_file.write_text(body, encoding="utf-8")


def test_minimal_source_uses_defaults(settings: Settings) -> None:
    write_sources(settings, """
sources:
  - name: Slo-Tech
    url: https://slo-tech.com/rss
""")
    source = load_sources(settings)[0]
    assert source.kind == "rss"
    assert source.channel_id == settings.default_channel_id
    assert source.interval == settings.poll_interval
    assert source.enabled is True


def test_defaults_block_and_channel_alias(settings: Settings) -> None:
    write_sources(settings, """
channels:
  tech: 999
defaults:
  interval: 120
  channel: tech
sources:
  - name: A
    url: https://a.example/rss
  - name: B
    url: https://b.example/rss
    interval: 60
    channel: 555
""")
    first, second = load_sources(settings)
    assert (first.interval, first.channel_id) == (120, 999)
    assert (second.interval, second.channel_id) == (60, 555)


def test_html_source_gets_fallback_selectors(settings: Settings) -> None:
    write_sources(settings, """
sources:
  - name: Blog
    url: https://blog.example
    type: html
""")
    assert load_sources(settings)[0].selectors["item"] == "article"


def test_filters_compile_at_load_time(settings: Settings) -> None:
    write_sources(settings, """
sources:
  - name: A
    url: https://a.example/rss
    filters:
      exclude: "oglasno(sporocilo"
""")
    with pytest.raises(ConfigError, match="invalid regex"):
        load_sources(settings)


def test_unknown_channel_name_is_rejected(settings: Settings) -> None:
    write_sources(settings, """
sources:
  - name: A
    url: https://a.example/rss
    channel: nowhere
""")
    with pytest.raises(ConfigError, match="neither a channel ID"):
        load_sources(settings)


def test_missing_channel_without_default_is_rejected(settings: Settings) -> None:
    settings.default_channel_id = None
    write_sources(settings, """
sources:
  - name: A
    url: https://a.example/rss
""")
    with pytest.raises(ConfigError, match="DISCORD_CHANNEL_ID"):
        load_sources(settings)


def test_duplicate_names_are_rejected(settings: Settings) -> None:
    write_sources(settings, """
sources:
  - name: A
    url: https://a.example/rss
  - name: A
    url: https://b.example/rss
""")
    with pytest.raises(ConfigError, match="duplicate source names"):
        load_sources(settings)


def test_missing_file_names_the_path(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_sources(settings)


@pytest.mark.parametrize(("raw", "expected"), [("#2b7fff", 0x2B7FFF), ("ff0000", 0xFF0000), (255, 255), (None, None)])
def test_parse_color(raw: object, expected: int | None) -> None:
    assert parse_color(raw) == expected


def test_parse_color_rejects_nonsense() -> None:
    with pytest.raises(ConfigError):
        parse_color("burgundy")
