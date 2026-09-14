from __future__ import annotations

from pathlib import Path

import pytest

from newsbot import sourcefile
from newsbot.config import ConfigError

STARTING_FILE = """# Watch list, edit freely.
channels:
  tech: 999

defaults:
  interval: 600

sources:
  # the important one
  - name: Slo-Tech
    url: https://slo-tech.com/rss
"""


@pytest.fixture
def path(tmp_path: Path) -> Path:
    target = tmp_path / "sources.yml"
    target.write_text(STARTING_FILE, encoding="utf-8")
    return target


def test_append_keeps_user_comments(path: Path) -> None:
    sourcefile.append_source(path, {"name": "Phoronix", "url": "https://www.phoronix.com/rss.php", "type": "rss"})
    text = path.read_text(encoding="utf-8")

    assert "# Watch list, edit freely." in text
    assert "# the important one" in text
    assert "Phoronix" in text
    assert sourcefile.source_names(path) == ["Slo-Tech", "Phoronix"]


def test_append_rejects_a_duplicate_name(path: Path) -> None:
    with pytest.raises(ConfigError, match="already exists"):
        sourcefile.append_source(path, {"name": "slo-tech", "url": "https://slo-tech.com/rss"})


def test_remove_source(path: Path) -> None:
    assert sourcefile.remove_source(path, "Slo-Tech") is True
    assert sourcefile.source_names(path) == []
    assert sourcefile.remove_source(path, "Slo-Tech") is False


def test_remove_is_case_insensitive(path: Path) -> None:
    assert sourcefile.remove_source(path, "SLO-TECH") is True


def test_set_enabled_writes_the_flag(path: Path) -> None:
    assert sourcefile.set_enabled(path, "Slo-Tech", False) is True
    assert "enabled: false" in path.read_text(encoding="utf-8")
    assert sourcefile.set_enabled(path, "nope", False) is False


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        sourcefile.source_names(tmp_path / "absent.yml")
