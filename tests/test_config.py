"""Tests for Step 1: config resolve and validate."""
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.config import Domain, ConfigError, resolve_config


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

    assert dict(config.notebook_map) == {}
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

    assert dict(config.notebook_map) == {"alpha": "nb-alpha-id"}
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


def test_rejects_relative_vault_path(write_claude_md):
    claude_md = write_claude_md({
        "vault_path": "relative/path/vault",
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    with pytest.raises(ConfigError, match="absolute"):
        resolve_config(claude_md)


def test_rejects_non_string_vault_path(write_claude_md):
    claude_md = write_claude_md({
        "vault_path": ["not", "a", "string"],
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    with pytest.raises(ConfigError, match=r"vault_path"):
        resolve_config(claude_md)


def test_rejects_missing_classification_mode(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match=r"missing required field 'classification_mode'"):
        resolve_config(claude_md)


def test_rejects_missing_routing_mode(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
    })

    with pytest.raises(ConfigError, match=r"missing required field 'routing_mode'"):
        resolve_config(claude_md)


def test_rejects_invalid_classification_mode_value(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "freeform",
        "routing_mode": "para",
    })

    with pytest.raises(ConfigError, match="unsupported"):
        resolve_config(claude_md)


def test_rejects_invalid_routing_mode_value(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "gtd",
    })

    with pytest.raises(ConfigError, match="unsupported"):
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


def test_rejects_domain_entry_missing_slug(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"description": "no slug here"}],
    })

    with pytest.raises(ConfigError, match=r"slug"):
        resolve_config(claude_md)


def test_rejects_domain_entry_missing_description(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha"}],
    })

    with pytest.raises(ConfigError, match=r"description"):
        resolve_config(claude_md)


def test_rejects_domain_entry_that_is_not_a_mapping(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": ["alpha"],
    })

    with pytest.raises(ConfigError, match=r"domain"):
        resolve_config(claude_md)


def test_rejects_missing_config_block(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Vault\n\nJust some prose, no config block.\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Vault Config"):
        resolve_config(claude_md)


def test_rejects_multiple_config_blocks(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n"
        "## Vault Config\n\n```yaml\nkey: a\n```\n\n"
        "## Vault Config\n\n```yaml\nkey: b\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"multiple"):
        resolve_config(claude_md)


def test_rejects_unterminated_yaml_fence(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\nvault_path: /tmp/x\nclassification_mode: emergent\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"closed|unterminated|closing"):
        resolve_config(claude_md)


def test_rejects_heading_without_yaml_fence(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\nJust some prose, no fenced block.\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"yaml"):
        resolve_config(claude_md)


def test_rejects_malformed_yaml_in_config_block(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\nkey: value:\n  not: : valid\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="YAML|parse"):
        resolve_config(claude_md)


def test_rejects_non_mapping_yaml_root_scalar(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\njust-a-scalar\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"mapping|object|dict"):
        resolve_config(claude_md)


def test_rejects_non_mapping_yaml_root_list(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\n- item1\n- item2\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"mapping|object|dict"):
        resolve_config(claude_md)


def test_resolves_config_with_crlf_line_endings(tmp_path):
    yaml_block = (
        "vault_path: " + str(tmp_path).replace("\\", "/") + "\r\n"
        "classification_mode: emergent\r\n"
        "routing_mode: emergent\r\n"
    )
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(
        ("# Vault\r\n\r\n## Vault Config\r\n\r\n```yaml\r\n" + yaml_block + "```\r\n").encode("utf-8")
    )

    config = resolve_config(claude_md)

    assert config.mode == "emergent"


def test_resolves_config_with_trailing_whitespace_on_fence_opener(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    vault_str = str(tmp_path).replace("\\", "/")
    claude_md.write_text(
        f"# Vault\n\n## Vault Config\n\n```yaml   \nvault_path: {vault_str}\n"
        "classification_mode: emergent\nrouting_mode: emergent\n```\n",
        encoding="utf-8",
    )

    config = resolve_config(claude_md)

    assert config.mode == "emergent"


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


def test_notebook_map_is_immutable(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": "x"}],
        "notebook_map": {"alpha": "nb-alpha-id"},
    })

    config = resolve_config(claude_md)

    assert isinstance(config.notebook_map, MappingProxyType)
    with pytest.raises(TypeError):
        config.notebook_map["alpha"] = "tampered"  # type: ignore[index]


def test_classification_confidence_threshold_default(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
    })

    config = resolve_config(claude_md)

    assert config.classification_confidence_threshold == 0.6


def test_classification_confidence_threshold_explicit(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": "x"}],
        "classification_confidence_threshold": 0.75,
    })

    config = resolve_config(claude_md)

    assert config.classification_confidence_threshold == 0.75


def test_rejects_classification_confidence_threshold_below_zero(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
        "classification_confidence_threshold": -0.1,
    })

    with pytest.raises(ConfigError, match=r"classification_confidence_threshold"):
        resolve_config(claude_md)


def test_rejects_classification_confidence_threshold_above_one(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
        "classification_confidence_threshold": 1.5,
    })

    with pytest.raises(ConfigError, match=r"classification_confidence_threshold"):
        resolve_config(claude_md)


def test_rejects_non_string_domain_slug(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": 42, "description": "ok"}],
    })

    with pytest.raises(ConfigError, match=r"slug"):
        resolve_config(claude_md)


def test_rejects_empty_domain_slug(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "   ", "description": "ok"}],
    })

    with pytest.raises(ConfigError, match=r"slug"):
        resolve_config(claude_md)


def test_rejects_non_string_domain_description(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": 99}],
    })

    with pytest.raises(ConfigError, match=r"description"):
        resolve_config(claude_md)


def test_rejects_empty_domain_description(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": ""}],
    })

    with pytest.raises(ConfigError, match=r"description"):
        resolve_config(claude_md)


def test_rejects_classification_confidence_threshold_non_numeric(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "emergent",
        "classification_confidence_threshold": "high",
    })

    with pytest.raises(ConfigError, match=r"classification_confidence_threshold"):
        resolve_config(claude_md)
