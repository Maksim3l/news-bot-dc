"""Edits sources.yml in place. Round-trip parsing keeps the user's comments and formatting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from newsbot.config import ConfigError, yaml_parser


def read_document(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"{path} not found")
    return yaml_parser().load(path.read_text(encoding="utf-8")) or {}


def write_document(path: Path, document: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml_parser().dump(document, handle)


def source_names(path: Path) -> list[str]:
    document = read_document(path)
    return [str(entry.get("name", "")) for entry in (document.get("sources") or [])]


def append_source(path: Path, entry: dict[str, Any]) -> None:
    document = read_document(path)
    entries = document.setdefault("sources", [])
    name = str(entry.get("name", "")).strip().lower()
    if any(str(existing.get("name", "")).strip().lower() == name for existing in entries):
        raise ConfigError(f"a source named {entry['name']!r} already exists")
    entries.append(entry)
    write_document(path, document)


def remove_source(path: Path, name: str) -> bool:
    document = read_document(path)
    entries = document.get("sources") or []
    keep = [entry for entry in entries if str(entry.get("name", "")).strip().lower() != name.strip().lower()]
    if len(keep) == len(entries):
        return False
    document["sources"] = keep
    write_document(path, document)
    return True


def set_enabled(path: Path, name: str, enabled: bool) -> bool:
    document = read_document(path)
    for entry in document.get("sources") or []:
        if str(entry.get("name", "")).strip().lower() == name.strip().lower():
            entry["enabled"] = enabled
            write_document(path, document)
            return True
    return False
