"""Step 5: generate frontmatter (mode-dependent).

Populates the canonical OS-wide frontmatter baseline (architecture
plan Section 1.4.1) plus track-specific additions (build spec lines
118-153). The fixed_domains track is implemented in v1; emergent mode
raises NotImplementedError until the parallel track ships.

The function is a deterministic rule-based builder. Inputs are the
upstream pipeline results (`Config`, `DetectionResult`,
`RefinedContent | None`, `ClassificationResult`, `ParaResult`) plus
capture metadata (`source_type`, `source_uri`, `captured_at`). The
function does not call models. Title generation uses a kebab-case
slug heuristic from the input's H1 or first sentence; the skill
orchestrator confirms or overrides before file write.

Function-side gate is unconditional. The skill orchestrator decides
whether to invoke based on `config.mode`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

import yaml

from .classify import ClassificationResult
from .config import Config
from .detect import DetectionResult
from .para import ParaResult
from .refine import RefinedContent


SourceType = Literal[
    "vault",
    "paste",
    "stdin",
    "api",
    "external_cli",
    "other",
]

# Fixed_domains track type enum per build spec line 128.
NoteType = Literal[
    "note",
    "session",
    "insight",
    "workflow",
    "prompt",
    "context",
    "project",
    "reference",
]


# Maximum kebab-case title length (build spec line 126: "max ~80 chars").
_TITLE_MAX_CHARS = 80

# Maximum number of tags per note (build spec line 130: "1-5 specific tags").
_MAX_TAGS = 5

# Marker block appended below the refined body when refinement changes
# the input. The skill orchestrator assembles the markdown body around
# this marker; the frontmatter only records whether the marker is
# expected so downstream consumers can audit refinement provenance.
_ORIGINAL_REF_MARKER = "## Captura original"

_PROCESSED_BY = "/vault-intake"
_SCHEMA_VERSION = "1.0"

_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_END_PATTERN = re.compile(r"[.!?]+\s")


@dataclass(frozen=True)
class Frontmatter:
    # OS-wide baseline (architecture plan Section 1.4.1)
    schema_version: str
    source_type: SourceType
    source_uri: str
    captured_at: str
    processed_by: str
    confidence: float | None
    original_ref: str

    # Cross-track conventions (both fixed_domains and emergent shapes
    # carry these per build spec lines 122-149)
    title: str
    date: str

    # Fixed_domains track additions (build spec line 128 closes `type`
    # to a fixed enum; emergent track will use a parallel shape with
    # `theme` instead of `domain` and an open `type` enum)
    type: NoteType
    domain: str
    tags: tuple[str, ...]
    notebook: str
    source_id: str
    project: str

    def to_yaml(self) -> str:
        """Emit YAML-frontmatter-ready text for the canonical fields.

        Field order matches the architecture plan baseline (OS-wide
        first, then cross-track, then track-specific) so diffs stay
        readable. None confidence emits as an empty string per the
        baseline's "optional" rule.
        """
        ordered: list[tuple[str, object]] = [
            ("schema_version", self.schema_version),
            ("source_type", self.source_type),
            ("source_uri", self.source_uri),
            ("captured_at", self.captured_at),
            ("processed_by", self.processed_by),
            ("confidence", "" if self.confidence is None else self.confidence),
            ("original_ref", self.original_ref),
            ("title", self.title),
            ("date", self.date),
            ("type", self.type),
            ("domain", self.domain),
            ("tags", list(self.tags)),
            ("notebook", self.notebook),
            ("source_id", self.source_id),
            ("project", self.project),
        ]
        return yaml.safe_dump(
            dict(ordered),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def generate_frontmatter(
    text: str,
    detection: DetectionResult,
    refinement: RefinedContent | None,
    classification: ClassificationResult,
    para: ParaResult,
    config: Config,
    *,
    source_type: SourceType = "paste",
    source_uri: str = "",
    captured_at: str | None = None,
) -> Frontmatter:
    if config.mode == "emergent":
        raise NotImplementedError(
            "emergent mode frontmatter is not implemented in v1; "
            "use classification_mode: fixed_domains for now"
        )

    captured = captured_at or date.today().isoformat()
    note_date = captured.split("T", 1)[0]

    title = _build_title(text, fallback_date=note_date)
    tags = _build_tags(classification)
    notebook = config.notebook_map.get(classification.primary, "")
    project = para.project_slug or "" if para.category == "project" else ""
    original_ref = (
        _ORIGINAL_REF_MARKER if refinement is not None and refinement.changed else ""
    )

    return Frontmatter(
        schema_version=_SCHEMA_VERSION,
        source_type=source_type,
        source_uri=source_uri,
        captured_at=captured,
        processed_by=_PROCESSED_BY,
        confidence=classification.confidence,
        original_ref=original_ref,
        title=title,
        date=note_date,
        type=detection.type,
        domain=classification.primary,
        tags=tags,
        notebook=notebook,
        source_id="",
        project=project,
    )


def _build_title(text: str, *, fallback_date: str) -> str:
    source = _extract_title_source(text)
    slug = _slugify(source)
    if not slug:
        return f"note-{fallback_date}"
    return slug


def _extract_title_source(text: str) -> str:
    match = _H1_PATTERN.search(text)
    if match:
        return match.group(1)
    stripped = text.strip()
    if not stripped:
        return ""
    sentence_break = _SENTENCE_END_PATTERN.search(stripped)
    if sentence_break:
        return stripped[: sentence_break.start()]
    return stripped


def _slugify(source: str) -> str:
    if not source:
        return ""
    # NFKD-normalize and strip combining accents so Portuguese inputs
    # ("Reunião") become ASCII-safe filenames ("reuniao").
    normalized = unicodedata.normalize("NFKD", source)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Replace any run of non-alphanumeric characters with a single
    # hyphen, then trim leading/trailing hyphens.
    slugged = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(slugged) > _TITLE_MAX_CHARS:
        slugged = slugged[:_TITLE_MAX_CHARS].rstrip("-")
    return slugged


def _build_tags(classification: ClassificationResult) -> tuple[str, ...]:
    if classification.uncertain:
        return ()
    seeded = (classification.primary,) + classification.secondary
    return seeded[:_MAX_TAGS]
