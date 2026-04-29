"""Step 1: config resolve and validate.

Reads vault config from a CLAUDE.md file's `## Vault Config` YAML block,
validates it under the Option Z mode-pair rule, and returns a frozen Config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

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
    notebook_map: Mapping[str, str]
    language: str
    skip_notebooklm: bool
    refinement_enabled: bool
    classification_confidence_threshold: float


class ConfigError(Exception):
    """Raised when vault CLAUDE.md config is missing, malformed, or invalid."""


_HEADING = re.compile(r"^##\s+Vault Config\s*$", re.MULTILINE)
_FENCE_OPEN = re.compile(r"\s*```yaml[ \t]*\n")
_FENCE_CLOSE = re.compile(r"^```\s*$", re.MULTILINE)

_VALID_PAIRS: dict[tuple[str, str], Mode] = {
    ("fixed_domains", "para"): "fixed_domains",
    ("emergent", "emergent"): "emergent",
}


def resolve_config(claude_md_path: Path) -> Config:
    text = Path(claude_md_path).read_text(encoding="utf-8")
    yaml_text = _extract_yaml(text)

    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse YAML in '## Vault Config' block: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"'## Vault Config' YAML must be a mapping (object), got {type(raw).__name__}"
        )

    vault_path = _validate_vault_path(raw)

    if "classification_mode" not in raw:
        raise ConfigError("missing required field 'classification_mode' in vault config")
    if "routing_mode" not in raw:
        raise ConfigError("missing required field 'routing_mode' in vault config")

    pair = (raw["classification_mode"], raw["routing_mode"])
    mode = _VALID_PAIRS.get(pair)
    if mode is None:
        raise ConfigError(
            f"unsupported (classification_mode, routing_mode) pair: {pair!r}; "
            "supported pairs are ('fixed_domains', 'para') and ('emergent', 'emergent')"
        )

    domains = _parse_domains(raw.get("domains") or ())
    if mode == "fixed_domains" and not domains:
        raise ConfigError(
            "fixed_domains mode requires non-empty 'domains' list in vault config"
        )

    threshold = _validate_confidence_threshold(raw)

    return Config(
        vault_path=vault_path,
        mode=mode,
        domains=domains,
        notebook_map=MappingProxyType(dict(raw.get("notebook_map") or {})),
        language=raw.get("language", "en"),
        skip_notebooklm=bool(raw.get("skip_notebooklm", False)),
        refinement_enabled=bool(raw.get("refinement_enabled", True)),
        classification_confidence_threshold=threshold,
    )


def _extract_yaml(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    headings = list(_HEADING.finditer(normalized))
    if not headings:
        raise ConfigError(
            "vault CLAUDE.md missing required '## Vault Config' heading"
        )
    if len(headings) > 1:
        raise ConfigError(
            "vault CLAUDE.md contains multiple '## Vault Config' blocks; only one is allowed"
        )

    after_heading = normalized[headings[0].end():]
    fence_open = _FENCE_OPEN.match(after_heading)
    if fence_open is None:
        raise ConfigError(
            "'## Vault Config' heading must be followed by a fenced ```yaml block"
        )

    body_start = fence_open.end()
    fence_close = _FENCE_CLOSE.search(after_heading, body_start)
    if fence_close is None:
        raise ConfigError(
            "'## Vault Config' YAML fence is not closed; expected a closing ``` line"
        )

    return after_heading[body_start:fence_close.start()]


def _validate_vault_path(raw: dict[str, Any]) -> Path:
    vault_path_raw = raw.get("vault_path")
    if not vault_path_raw:
        raise ConfigError("missing required field 'vault_path' in vault config")
    if not isinstance(vault_path_raw, str):
        raise ConfigError(
            f"'vault_path' must be a string, got {type(vault_path_raw).__name__}"
        )
    vault_path = Path(vault_path_raw)
    if not vault_path.is_absolute():
        raise ConfigError(
            f"'vault_path' must be absolute, got {vault_path_raw!r}"
        )
    return vault_path


def _validate_confidence_threshold(raw: dict[str, Any]) -> float:
    if "classification_confidence_threshold" not in raw:
        return 0.6
    value = raw["classification_confidence_threshold"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            "'classification_confidence_threshold' must be a number between 0 and 1, "
            f"got {type(value).__name__}"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise ConfigError(
            "'classification_confidence_threshold' must be between 0 and 1, "
            f"got {value!r}"
        )
    return float(value)


def _parse_domains(raw: object) -> tuple[Domain, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(
            f"'domains' must be a list, got {type(raw).__name__}"
        )
    parsed: list[Domain] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"domain entry at index {index} must be a mapping, got {type(entry).__name__}"
            )
        if "slug" not in entry:
            raise ConfigError(
                f"domain entry at index {index} missing required 'slug' field"
            )
        if "description" not in entry:
            raise ConfigError(
                f"domain entry at index {index} missing required 'description' field"
            )
        slug = entry["slug"]
        if not isinstance(slug, str) or not slug.strip():
            raise ConfigError(
                f"domain entry at index {index} 'slug' must be a non-empty string, "
                f"got {type(slug).__name__}: {slug!r}"
            )
        description = entry["description"]
        if not isinstance(description, str) or not description.strip():
            raise ConfigError(
                f"domain entry at index {index} 'description' must be a non-empty string, "
                f"got {type(description).__name__}: {description!r}"
            )
        parsed.append(Domain(slug=slug, description=description))
    return tuple(parsed)
