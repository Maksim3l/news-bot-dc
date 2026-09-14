"""Finds the feed behind a page so adding a site only takes its homepage URL."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")
COMMON_FEED_PATHS = ("/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml")


def looks_like_feed(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def discover_feed_url(body: bytes, page_url: str) -> str | None:
    """Reads <link rel="alternate"> from a homepage. Returns None when the site publishes no feed."""
    soup = BeautifulSoup(body, "lxml")
    for link in soup.find_all("link", rel=lambda value: value and "alternate" in value):
        if str(link.get("type", "")).lower() in FEED_TYPES and link.get("href"):
            return urljoin(page_url, link["href"])
    return None


def guess_title(body: bytes, fallback: str) -> str:
    soup = BeautifulSoup(body, "lxml")
    site_name = soup.find("meta", property="og:site_name")
    if site_name and site_name.get("content"):
        return str(site_name["content"]).strip()
    if soup.title and soup.title.string:
        return str(soup.title.string).strip().split("|")[0].split("-")[0].strip() or fallback
    return fallback


def candidate_feed_urls(page_url: str) -> list[str]:
    return [urljoin(page_url, path) for path in COMMON_FEED_PATHS]
