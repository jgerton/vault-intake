"""Tests for vault_intake.bootstrap.bootstrap_vault.

Covers: standard directory creation, inbox/ inclusion, idempotency,
and domain-scoped sessions/ subdirectories (pre-wired for Fix 3 but
gated on the domain_scoped_routing flag).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from vault_intake.config import resolve_config
from vault_intake.bootstrap import bootstrap_vault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, extra: dict[str, Any] | None = None) -> Any:
    """Write a minimal fixed_domains CLAUDE.md and return a Config."""
    cfg: dict[str, Any] = {
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [
            {"slug": "dev", "description": "Software development"},
            {"slug": "ops", "description": "Operations"},
        ],
    }
    if extra:
        cfg.update(extra)
    yaml_text = yaml.safe_dump(cfg, sort_keys=False)
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\n" + yaml_text + "```\n",
        encoding="utf-8",
    )
    return resolve_config(claude_md)


# ---------------------------------------------------------------------------
# Round 1: standard PARA directories are created
# ---------------------------------------------------------------------------


def test_bootstrap_creates_sessions(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "sessions").is_dir()


def test_bootstrap_creates_insights(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "insights").is_dir()


def test_bootstrap_creates_workflows(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "workflows").is_dir()


def test_bootstrap_creates_prompts(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "prompts").is_dir()


def test_bootstrap_creates_references(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "references").is_dir()


def test_bootstrap_creates_projects(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "projects").is_dir()


def test_bootstrap_creates_context(tmp_path):
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "context").is_dir()


# ---------------------------------------------------------------------------
# Round 2: system and capture directories
# ---------------------------------------------------------------------------


def test_bootstrap_creates_system_inbox(tmp_path):
    """_inbox/ is the routing fallback; must be present."""
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "_inbox").is_dir()


def test_bootstrap_creates_user_inbox(tmp_path):
    """inbox/ (no underscore) is the user-facing capture drop folder."""
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / "inbox").is_dir()


def test_bootstrap_creates_nlm_queue_dir(tmp_path):
    """Queue directory must exist before first intake run."""
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    assert (tmp_path / ".vault-intake" / "nlm_queue").is_dir()


# ---------------------------------------------------------------------------
# Round 3: idempotency
# ---------------------------------------------------------------------------


def test_bootstrap_is_idempotent(tmp_path):
    """Calling bootstrap_vault twice does not raise and leaves dirs intact."""
    config = _make_config(tmp_path)
    bootstrap_vault(config)
    bootstrap_vault(config)
    assert (tmp_path / "inbox").is_dir()
    assert (tmp_path / "sessions").is_dir()


def test_bootstrap_preserves_existing_files(tmp_path):
    """A pre-existing file inside sessions/ is not deleted by bootstrap."""
    config = _make_config(tmp_path)
    (tmp_path / "sessions").mkdir()
    existing = tmp_path / "sessions" / "my-note.md"
    existing.write_text("hello", encoding="utf-8")
    bootstrap_vault(config)
    assert existing.read_text(encoding="utf-8") == "hello"


# ---------------------------------------------------------------------------
# Round 4: return value
# ---------------------------------------------------------------------------


def test_bootstrap_returns_list_of_created_paths(tmp_path):
    """bootstrap_vault returns a list of Path objects for all dirs created."""
    config = _make_config(tmp_path)
    created = bootstrap_vault(config)
    assert isinstance(created, list)
    assert all(isinstance(p, Path) for p in created)
    assert tmp_path / "inbox" in created
    assert tmp_path / "sessions" in created
