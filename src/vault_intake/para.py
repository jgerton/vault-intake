"""Step 4: categorize content into a PARA category.

fixed_domains/para mode (v1): rule-based heuristics inspecting the raw
text plus the upstream `DetectionResult` and `ClassificationResult`.
Emergent mode raises NotImplementedError; emergent routing is theme-
based per Step 8 and skips PARA entirely.

PARA categories per build spec lines 113-116:

- project: input references an active project slug found in `projects/`
- area: about ongoing responsibility in a domain, no project mention
- resource: external source material (reference content type, URLs)
- archive: superseded-decision phrasing flagged for user review

Category priority when multiple strong signals fire:
project > resource > archive > area. The winning category determines
routing, but `signals` records every signal that fired so the audit
trail is preserved, and `uncertain` flips True whenever more than one
strong signal competes.

Function-side gate is unconditional. The skill orchestrator decides
whether to call `categorize_para()` based on `config.mode`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .classify import ClassificationResult
from .config import Config
from .detect import DetectionResult


ParaCategory = Literal["project", "area", "resource", "archive"]


@dataclass(frozen=True)
class ParaResult:
    category: ParaCategory
    project_slug: str | None
    uncertain: bool
    signals: tuple[str, ...]


# Substring phrases (case-insensitive) signaling superseded or
# deprecated decisions. Matches build spec line 116 "Archive candidate".
_ARCHIVE_PHRASES: tuple[str, ...] = (
    "we used to",
    "old approach was",
    "deprecated",
    "no longer used",
    "superseded",
)


def categorize_para(
    text: str,
    detection: DetectionResult,
    classification: ClassificationResult,
    config: Config,
) -> ParaResult:
    if config.mode == "emergent":
        raise NotImplementedError(
            "emergent mode skips PARA categorization entirely; "
            "Step 8 handles routing via theme inference instead"
        )

    project_slug = _detect_project_slug(text, config.vault_path)
    is_reference = detection.type == "reference"
    has_archive_phrasing = _has_archive_phrasing(text)

    strong_signals: list[str] = []
    if project_slug is not None:
        strong_signals.append("project_slug_match")
    if is_reference:
        strong_signals.append("reference_content_type")
    if has_archive_phrasing:
        strong_signals.append("archive_phrasing")

    if project_slug is not None:
        category: ParaCategory = "project"
    elif is_reference:
        category = "resource"
    elif has_archive_phrasing:
        category = "archive"
    else:
        category = "area"

    descriptive_signals: list[str] = []
    domain_in_scope = (
        category == "area"
        and not classification.uncertain
        and classification.primary in {d.slug for d in config.domains}
    )
    if domain_in_scope:
        descriptive_signals.append("domain_in_scope")

    multiple_strong = len(strong_signals) > 1
    area_without_evidence = category == "area" and not domain_in_scope
    uncertain = multiple_strong or area_without_evidence

    return ParaResult(
        category=category,
        project_slug=project_slug,
        uncertain=uncertain,
        signals=tuple(strong_signals + descriptive_signals),
    )


def _detect_project_slug(text: str, vault_path: Path) -> str | None:
    projects_dir = vault_path / "projects"
    if not projects_dir.is_dir():
        return None

    for slug in sorted(_iter_project_slugs(projects_dir)):
        if _slug_mentioned(text, slug):
            return slug
    return None


def _iter_project_slugs(projects_dir: Path) -> Iterable[str]:
    for entry in projects_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix == ".md":
            yield entry.stem
        elif entry.is_dir():
            yield entry.name


def _slug_mentioned(text: str, slug: str) -> bool:
    # Exclude hyphens from the boundary so a shorter slug like
    # "launch-redesign" does not spuriously match inside a longer
    # hyphenated slug like "launch-redesign-extended" mentioned in
    # the input. Without this, the alphabetically-first iteration
    # in `_detect_project_slug` would attribute the longer mention
    # to the shorter project.
    pattern = re.compile(rf"(?<![\w-]){re.escape(slug)}(?![\w-])", re.IGNORECASE)
    return bool(pattern.search(text))


def _has_archive_phrasing(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _ARCHIVE_PHRASES)
