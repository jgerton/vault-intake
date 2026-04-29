"""Step 1: config resolve and validate.

Reads vault config from a CLAUDE.md file's `## Vault Config` YAML block,
validates it under the Option Z mode-pair rule, and returns a frozen Config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class Domain:
    slug: str
    description: str


Mode = Literal["fixed_domains", "emergent"]


@dataclass(frozen=True)
class Config:
    vault_path: Path
    mode: Mode
    domains: tuple[Domain, ...]
    notebook_map: dict[str, str]
    language: str
    skip_notebooklm: bool
    refinement_enabled: bool


class ConfigError(Exception):
    """Raised when vault CLAUDE.md config is missing, malformed, or invalid."""


_YAML_BLOCK_PATTERN = re.compile(
    r"^##\s+Vault Config\s*\n+```yaml\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


def resolve_config(claude_md_path: Path) -> Config:
    text = Path(claude_md_path).read_text(encoding="utf-8")
    match = _YAML_BLOCK_PATTERN.search(text)
    if not match:
        raise ConfigError(
            "vault CLAUDE.md missing required '## Vault Config' YAML block"
        )
    try:
        raw = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse YAML in '## Vault Config' block: {exc}") from exc

    vault_path_raw = raw.get("vault_path")
    if not vault_path_raw:
        raise ConfigError("missing required field 'vault_path' in vault config")

    classification_mode = raw.get("classification_mode")
    routing_mode = raw.get("routing_mode")
    pair = (classification_mode, routing_mode)
    if pair == ("fixed_domains", "para"):
        mode: Mode = "fixed_domains"
    elif pair == ("emergent", "emergent"):
        mode = "emergent"
    else:
        raise ConfigError(
            f"unsupported (classification_mode, routing_mode) pair: {pair!r}; "
            "supported pairs are ('fixed_domains', 'para') and ('emergent', 'emergent')"
        )

    domains_raw = raw.get("domains") or ()
    domains = tuple(
        Domain(slug=d["slug"], description=d["description"]) for d in domains_raw
    )
    if mode == "fixed_domains" and not domains:
        raise ConfigError(
            "fixed_domains mode requires non-empty 'domains' list in vault config"
        )

    return Config(
        vault_path=Path(vault_path_raw),
        mode=mode,
        domains=domains,
        notebook_map=dict(raw.get("notebook_map") or {}),
        language=raw.get("language", "en"),
        skip_notebooklm=bool(raw.get("skip_notebooklm", False)),
        refinement_enabled=bool(raw.get("refinement_enabled", True)),
    )
