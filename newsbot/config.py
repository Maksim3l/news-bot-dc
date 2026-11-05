"""Configuration layer: .env for secrets and runtime knobs, sources.yml for what to watch."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from ruamel.yaml import YAML

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; news-bot-dc/1.0; +https://github.com/Maksim3l/news-bot-dc)"
)

# Used when an html source names no selectors of its own.
FALLBACK_SELECTORS: dict[str, str] = {
    "item": "article",
    "title": "h1, h2, h3",
    "link": "a@href",
}


class ConfigError(RuntimeError):
    """Raised for anything the user can fix by editing .env or sources.yml."""


def yaml_parser() -> YAML:
    """Round-trip mode keeps the user's comments and ordering when the bot rewrites sources.yml."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


@dataclass(slots=True)
class Settings:
    token: str
    default_channel_id: int | None
    guild_id: int | None
    application_id: int | None
    sources_file: Path
    digest_file: Path
    database_path: Path
    poll_interval: int
    max_items: int
    announce_backlog: bool
    http_timeout: int
    retention_days: int
    user_agent: str
    log_level: str


@dataclass(slots=True)
class FilterRules:
    include: tuple[re.Pattern[str], ...] = ()
    exclude: tuple[re.Pattern[str], ...] = ()


@dataclass(slots=True)
class SourceConfig:
    name: str
    url: str
    kind: str = "rss"
    channel_id: int = 0
    interval: int = 600
    max_items: int = 5
    color: int | None = None
    enabled: bool = True
    selectors: dict[str, str] = field(default_factory=dict)
    filters: FilterRules = field(default_factory=FilterRules)
    headers: dict[str, str] = field(default_factory=dict)
    language: str = "en"
    post: bool = True


def _env_text(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int | None) -> int | None:
    raw = _env_text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


def _env_flag(name: str, default: bool) -> bool:
    raw = _env_text(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load_settings(env_file: str | Path = ".env", require_token: bool = True) -> Settings:
    load_dotenv(env_file, override=False)

    token = _env_text("DISCORD_TOKEN")
    if require_token and not token:
        raise ConfigError("DISCORD_TOKEN is empty. Copy .env.example to .env and paste your bot token.")

    return Settings(
        token=token,
        default_channel_id=_env_int("DISCORD_CHANNEL_ID", None),
        guild_id=_env_int("DISCORD_GUILD_ID", None),
        application_id=_env_int("DISCORD_APPLICATION_ID", None),
        sources_file=Path(_env_text("SOURCES_FILE", "sources.yml")),
        digest_file=Path(_env_text("DIGEST_FILE", "digest.yml")),
        database_path=Path(_env_text("DATABASE_PATH", "data/seen.sqlite3")),
        poll_interval=_env_int("POLL_INTERVAL", 600),
        max_items=_env_int("MAX_ITEMS_PER_POLL", 5),
        announce_backlog=_env_flag("ANNOUNCE_BACKLOG", False),
        http_timeout=_env_int("HTTP_TIMEOUT", 20),
        retention_days=_env_int("RETENTION_DAYS", 90),
        user_agent=_env_text("USER_AGENT", DEFAULT_USER_AGENT),
        log_level=_env_text("LOG_LEVEL", "INFO").upper(),
    )


def parse_color(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lstrip("#")
    try:
        return int(text, 16)
    except ValueError as exc:
        raise ConfigError(f"color {value!r} is not a hex value like '#2b7fff'") from exc


def _compile_patterns(values: Any, source_name: str, key: str) -> tuple[re.Pattern[str], ...]:
    if not values:
        return ()
    if isinstance(values, str):
        values = [values]
    patterns = []
    for raw in values:
        try:
            patterns.append(re.compile(str(raw), re.IGNORECASE))
        except re.error as exc:
            raise ConfigError(f"source {source_name!r}: {key} pattern {raw!r} is invalid regex ({exc})") from exc
    return tuple(patterns)


def _resolve_channel(value: Any, aliases: dict[str, int], fallback: int | None, source_name: str) -> int:
    if value in (None, ""):
        if fallback is None:
            raise ConfigError(
                f"source {source_name!r} has no channel and DISCORD_CHANNEL_ID is unset. "
                "Set one of them."
            )
        return fallback
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text in aliases:
        return aliases[text]
    if text.isdigit():
        return int(text)
    raise ConfigError(
        f"source {source_name!r} points at channel {text!r}, which is neither a channel ID "
        "nor a name listed under 'channels:' in sources.yml"
    )


def build_source(raw: dict[str, Any], defaults: dict[str, Any], aliases: dict[str, int], settings: Settings) -> SourceConfig:
    merged = {**defaults, **raw}
    name = str(merged.get("name") or "").strip()
    if not name:
        raise ConfigError("every source needs a 'name'")
    url = str(merged.get("url") or "").strip()
    if not url:
        raise ConfigError(f"source {name!r} needs a 'url'")

    kind = str(merged.get("type") or merged.get("kind") or "rss").strip().lower()
    selectors = dict(merged.get("selectors") or {})
    if kind == "html" and not selectors:
        selectors = dict(FALLBACK_SELECTORS)

    filters_raw = merged.get("filters") or {}
    filters = FilterRules(
        include=_compile_patterns(filters_raw.get("include"), name, "include"),
        exclude=_compile_patterns(filters_raw.get("exclude"), name, "exclude"),
    )

    return SourceConfig(
        name=name,
        url=url,
        kind=kind,
        channel_id=_resolve_channel(merged.get("channel"), aliases, settings.default_channel_id, name),
        interval=int(merged.get("interval") or settings.poll_interval),
        max_items=int(merged.get("max_items") or settings.max_items),
        color=parse_color(merged.get("color")),
        enabled=bool(merged.get("enabled", True)),
        selectors=selectors,
        filters=filters,
        headers=dict(merged.get("headers") or {}),
        language=str(merged.get("language") or "en").strip().lower(),
        post=bool(merged.get("post", True)),
    )


def read_yaml(path: Path, template: str) -> Any:
    if not path.exists():
        raise ConfigError(f"{path} not found. Copy {template} to {path} and edit it.")
    if path.is_dir():
        # docker creates a directory when a bind-mounted file is missing on the host
        raise ConfigError(f"{path} is a directory. Create the file on the host (cp {template} {path.name}) and restart.")
    return yaml_parser().load(path.read_text(encoding="utf-8")) or {}


def channel_aliases(document: Any) -> dict[str, int]:
    return {str(k): int(v) for k, v in (document.get("channels") or {}).items()}


def load_channel_aliases(settings: Settings) -> dict[str, int]:
    return channel_aliases(read_yaml(settings.sources_file, "sources.example.yml"))


def resolve_channel(value: Any, aliases: dict[str, int], fallback: int | None, owner: str) -> int:
    return _resolve_channel(value, aliases, fallback, owner)


def load_sources(settings: Settings) -> list[SourceConfig]:
    path = settings.sources_file
    document = read_yaml(path, "sources.example.yml")
    aliases = channel_aliases(document)
    defaults = dict(document.get("defaults") or {})
    entries = document.get("sources") or []
    if not entries:
        raise ConfigError(f"{path} lists no sources under 'sources:'")

    sources = [build_source(dict(entry), defaults, aliases, settings) for entry in entries]

    duplicates = {s.name for s in sources if sum(o.name == s.name for o in sources) > 1}
    if duplicates:
        raise ConfigError(f"duplicate source names in {path}: {', '.join(sorted(duplicates))}")
    return sources
