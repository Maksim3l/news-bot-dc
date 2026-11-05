"""Turns digest.yml into typed settings. Categories, schedule and model all live in that file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from newsbot.config import ConfigError, Settings, load_channel_aliases, read_yaml, resolve_channel

PROVIDERS = {"ollama", "anthropic"}
DEFAULT_PROVIDER = "ollama"
DEFAULT_MODELS = {"ollama": "qwen3.6:35b-a3b", "anthropic": "claude-haiku-5-5"}
# Models that accept fallbacks="default". Others (Haiku 5.5 included) get a 400 if it is sent.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
GOOGLE_NEWS_EN = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


@dataclass(slots=True)
class Category:
    key: str
    title: str
    brief: str
    prefer: tuple[re.Pattern[str], ...] = ()
    prefer_note: str = ""


@dataclass(slots=True)
class QuoteSettings:
    enabled: bool = True
    dataset: Path = Path("assets/office_quotes.csv")
    shortlist: int = 40


@dataclass(slots=True)
class EnglishSettings:
    enabled: bool = True
    search_url: str = GOOGLE_NEWS_EN
    results: int = 8


@dataclass(slots=True)
class OllamaSettings:
    url: str = "http://localhost:11434"
    # Context window in tokens. Ollama's default is small and cuts long prompts silently.
    num_ctx: int = 16384
    num_thread: int | None = None
    think: bool | None = False
    # Seconds one request may take. CPU-only prompt reading is slow.
    request_timeout: int = 1800
    pull_missing: bool = True


@dataclass(slots=True)
class DigestConfig:
    enabled: bool
    channel_id: int
    build_at: time
    build_deadline: time
    post_at: time
    timezone: ZoneInfo
    provider: str
    model: str
    effort: str
    refusal_fallback: bool
    window_hours: int
    max_age_hours: int
    max_candidates: int
    summary_chars: int
    keep_days: int
    post_when_empty: bool
    quality_bar: str
    categories: list[Category] = field(default_factory=list)
    quote: QuoteSettings = field(default_factory=QuoteSettings)
    english: EnglishSettings = field(default_factory=EnglishSettings)
    ollama: OllamaSettings = field(default_factory=OllamaSettings)

    def category(self, key: str) -> Category | None:
        return next((category for category in self.categories if category.key == key), None)


def _parse_time(value: Any, key: str, default: str) -> time:
    text = str(value or default).strip()
    try:
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"digest {key} {text!r} must look like 08:30") from exc


def _parse_zone(value: Any) -> ZoneInfo:
    name = str(value or "Europe/Ljubljana").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"digest timezone {name!r} is not an IANA zone like Europe/Ljubljana") from exc


def _patterns(values: Any, key: str) -> tuple[re.Pattern[str], ...]:
    if not values:
        return ()
    if isinstance(values, str):
        values = [values]
    try:
        return tuple(re.compile(str(raw), re.IGNORECASE) for raw in values)
    except re.error as exc:
        raise ConfigError(f"digest category {key!r}: prefer pattern is invalid regex ({exc})") from exc


def _build_category(raw: dict[str, Any]) -> Category:
    key = str(raw.get("key") or "").strip()
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise ConfigError(f"digest category key {key!r} must be lowercase letters, digits or _")
    brief = " ".join(str(raw.get("brief") or "").split())
    if not brief:
        raise ConfigError(f"digest category {key!r} needs a 'brief' describing what belongs in it")
    return Category(
        key=key,
        title=str(raw.get("title") or key.replace("_", " ").title()),
        brief=brief,
        prefer=_patterns(raw.get("prefer"), key),
        prefer_note=" ".join(str(raw.get("prefer_note") or "").split()),
    )


def load_digest(settings: Settings) -> DigestConfig:
    document = read_yaml(settings.digest_file, "digest.example.yml")
    aliases = load_channel_aliases(settings)

    categories = [_build_category(dict(entry)) for entry in document.get("categories") or []]
    keys = [category.key for category in categories]
    if len(keys) != len(set(keys)):
        raise ConfigError(f"duplicate digest category keys in {settings.digest_file}")

    effort = str(document.get("effort") or "medium").strip().lower()
    if effort not in EFFORT_LEVELS:
        raise ConfigError(f"digest effort {effort!r} must be one of {', '.join(sorted(EFFORT_LEVELS))}")

    quote_raw = dict(document.get("quote") or {})
    english_raw = dict(document.get("english") or {})
    enabled = bool(document.get("enabled", True))
    if enabled and not categories:
        raise ConfigError(f"{settings.digest_file} is enabled but lists no categories")

    provider = str(document.get("provider") or DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"digest provider {provider!r} must be one of {', '.join(sorted(PROVIDERS))}")
    model = str(document.get("model") or DEFAULT_MODELS[provider]).strip()

    build_at = _parse_time(document.get("build_at"), "build_at", "05:00")
    build_deadline = _parse_time(document.get("build_deadline"), "build_deadline", "06:30")
    post_at = _parse_time(document.get("post_at"), "post_at", "08:00")
    if not build_at < build_deadline <= post_at:
        raise ConfigError("digest times must be in order within one day: build_at < build_deadline <= post_at")

    ollama_raw = dict(document.get("ollama") or {})
    num_thread = ollama_raw.get("num_thread")
    think = ollama_raw.get("think", False)

    return DigestConfig(
        enabled=enabled,
        channel_id=resolve_channel(document.get("channel"), aliases, settings.default_channel_id, "digest"),
        build_at=build_at,
        build_deadline=build_deadline,
        post_at=post_at,
        timezone=_parse_zone(document.get("timezone")),
        provider=provider,
        model=model,
        effort=effort,
        refusal_fallback=provider == "anthropic" and bool(document.get("refusal_fallback", model in FALLBACK_MODELS)),
        window_hours=int(document.get("window_hours") or 24),
        max_age_hours=int(document.get("max_age_hours") or 48),
        max_candidates=int(document.get("max_candidates") or 150),
        summary_chars=int(document.get("summary_chars", 100) or 0),
        keep_days=int(document.get("keep_days") or 14),
        post_when_empty=bool(document.get("post_when_empty", False)),
        quality_bar=" ".join(str(document.get("quality_bar") or "").split()),
        categories=categories,
        quote=QuoteSettings(
            enabled=bool(quote_raw.get("enabled", True)),
            dataset=Path(str(quote_raw.get("dataset") or "assets/office_quotes.csv")),
            shortlist=int(quote_raw.get("shortlist") or 40),
        ),
        english=EnglishSettings(
            enabled=bool(english_raw.get("enabled", True)),
            search_url=str(english_raw.get("search_url") or GOOGLE_NEWS_EN),
            results=int(english_raw.get("results") or 8),
        ),
        ollama=OllamaSettings(
            # .env wins so the same digest.yml works inside and outside docker.
            url=(os.getenv("OLLAMA_URL") or ollama_raw.get("url") or "http://localhost:11434").rstrip("/"),
            num_ctx=int(ollama_raw.get("num_ctx") or 16384),
            num_thread=int(num_thread) if num_thread else None,
            think=None if think is None else bool(think),
            request_timeout=int(ollama_raw.get("request_timeout") or 1800),
            pull_missing=bool(ollama_raw.get("pull_missing", True)),
        ),
    )
