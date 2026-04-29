"""Shared pytest fixtures for vault-intake tests."""
from pathlib import Path
from typing import Any

import pytest
import yaml


def build_claude_md(config: dict[str, Any] | None, prose_before: str = "", prose_after: str = "") -> str:
    """Build CLAUDE.md content with a Vault Config YAML block.

    If config is None, no config block is included.
    """
    parts: list[str] = ["# Test Vault\n"]
    if prose_before:
        parts.append(prose_before + "\n")
    if config is not None:
        yaml_text = yaml.safe_dump(config, sort_keys=False)
        parts.append("## Vault Config\n\n```yaml\n" + yaml_text + "```\n")
    if prose_after:
        parts.append(prose_after + "\n")
    return "\n".join(parts)


@pytest.fixture
def write_claude_md(tmp_path: Path):
    """Return a callable that writes a CLAUDE.md to tmp_path with the given config."""

    def _write(config: dict[str, Any] | None = None, *, prose_before: str = "", prose_after: str = "") -> Path:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(build_claude_md(config, prose_before=prose_before, prose_after=prose_after), encoding="utf-8")
        return claude_md

    return _write
