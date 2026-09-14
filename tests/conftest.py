from __future__ import annotations

from pathlib import Path

import pytest

from newsbot.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token="test-token",
        default_channel_id=111,
        guild_id=None,
        sources_file=tmp_path / "sources.yml",
        database_path=tmp_path / "seen.sqlite3",
        poll_interval=600,
        max_items=5,
        announce_backlog=False,
        http_timeout=5,
        retention_days=90,
        user_agent="test-agent",
        log_level="INFO",
    )


@pytest.fixture
def rss_bytes() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>Slo-Tech</title>
      <item>
        <title>Nova graficna kartica</title>
        <link>https://slo-tech.com/novice/t111</link>
        <description>&lt;p&gt;Kratek opis novice.&lt;/p&gt;</description>
        <pubDate>Tue, 09 Sep 2025 10:00:00 +0200</pubDate>
      </item>
      <item>
        <title>Linux 6.18 izdan</title>
        <link>https://slo-tech.com/novice/t222</link>
        <description>Jedro je zunaj.</description>
        <pubDate>Mon, 08 Sep 2025 09:00:00 +0200</pubDate>
      </item>
    </channel></rss>"""


@pytest.fixture
def html_bytes() -> bytes:
    return b"""<html><body>
      <article class="post">
        <h2><a href="/news/one">First story</a></h2>
        <p class="excerpt">Summary of the <b>first</b> story.</p>
        <img src="/img/one.png"/>
        <time datetime="2025-09-09T08:30:00+00:00">yesterday</time>
      </article>
      <article class="post">
        <h2><a href="https://other.example/two">Second story</a></h2>
        <p class="excerpt">Summary two.</p>
      </article>
      <article class="advert"><h2>Ignore me</h2></article>
    </body></html>"""
