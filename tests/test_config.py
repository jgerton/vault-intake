"""Tests for Step 1: config resolve and validate."""
from pathlib import Path

import pytest

from vault_intake.config import Config, Domain, ConfigError, resolve_config


def test_resolves_fixed_domains_config_happy_path(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": "First test domain"}],
    })

    config = resolve_config(claude_md)

    assert config.mode == "fixed_domains"
    assert config.vault_path == tmp_path
    assert config.domains == (Domain(slug="alpha", description="First test domain"),)


def test_resolves_emergent_config_happy_path(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    config = resolve_config(claude_md)

    assert config.mode == "emergent"
    assert config.vault_path == tmp_path
    assert config.domains == ()


def test_applies_defaults_when_optional_fields_missing(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    config = resolve_config(claude_md)

    assert config.notebook_map == {}
    assert config.language == "en"
    assert config.skip_notebooklm is False
    assert config.refinement_enabled is True


def test_preserves_explicit_optional_fields(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": "x"}],
        "notebook_map": {"alpha": "nb-alpha-id"},
        "language": "pt-BR",
        "skip_notebooklm": True,
        "refinement_enabled": False,
    })

    config = resolve_config(claude_md)

    assert config.notebook_map == {"alpha": "nb-alpha-id"}
    assert config.language == "pt-BR"
    assert config.skip_notebooklm is True
    assert config.refinement_enabled is False


def test_rejects_missing_vault_path(write_claude_md):
    claude_md = write_claude_md({
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    with pytest.raises(ConfigError, match="vault_path"):
        resolve_config(claude_md)


def test_rejects_missing_classification_mode(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match="classification_mode"):
        resolve_config(claude_md)


def test_rejects_missing_routing_mode(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
    })

    with pytest.raises(ConfigError, match="routing_mode"):
        resolve_config(claude_md)


def test_rejects_invalid_classification_mode_value(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "freeform",
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match="classification_mode"):
        resolve_config(claude_md)


def test_rejects_invalid_routing_mode_value(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "gtd",
    })

    with pytest.raises(ConfigError, match="routing_mode"):
        resolve_config(claude_md)


def test_rejects_orthogonal_pair_emergent_para(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match="unsupported"):
        resolve_config(claude_md)


def test_rejects_orthogonal_pair_fixed_domains_emergent(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "emergent",
    })

    with pytest.raises(ConfigError, match="unsupported"):
        resolve_config(claude_md)


def test_rejects_fixed_domains_mode_without_domains_list(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match="domains"):
        resolve_config(claude_md)


def test_rejects_fixed_domains_mode_with_empty_domains_list(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [],
    })

    with pytest.raises(ConfigError, match="domains"):
        resolve_config(claude_md)


def test_emergent_mode_does_not_require_domains_list(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    config = resolve_config(claude_md)

    assert config.domains == ()


def test_rejects_missing_config_block(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Vault\n\nJust some prose, no config block.\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Vault Config"):
        resolve_config(claude_md)


def test_rejects_malformed_yaml_in_config_block(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\nkey: value:\n  not: : valid\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="YAML|parse"):
        resolve_config(claude_md)


def test_resolves_config_when_surrounded_by_prose(write_claude_md, tmp_path):
    claude_md = write_claude_md(
        {
            "vault_path": str(tmp_path),
            "classification_mode": "fixed_domains",
            "routing_mode": "para",
            "domains": [{"slug": "alpha", "description": "x"}],
        },
        prose_before="Some intro paragraph about this vault.",
        prose_after="## Other Section\n\nUnrelated notes about workflows.",
    )

    config = resolve_config(claude_md)

    assert config.mode == "fixed_domains"


def test_rejects_nonexistent_claude_md(tmp_path):
    with pytest.raises((FileNotFoundError, ConfigError)):
        resolve_config(tmp_path / "does_not_exist.md")
