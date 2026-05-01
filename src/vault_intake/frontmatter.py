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
from .detect import ContentType, DetectionResult
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


# Maximum kebab-case title length. Lowered from 80 to 60 on 2026-04-30
# after real-vault dogfood produced ugly mid-word-truncated filenames at
# the 80-char cap; build spec line 126's "max ~80 chars" was a ceiling,
# not a target.
_TITLE_MAX_CHARS = 60

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
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[.!?]+\s+")


# Translation from Step 1's 7-value detection enum to the
# fixed_domains frontmatter's 8-value type enum (build spec line 128).
# `document` and `transcription` are detection-stage signals that
# describe structure or format rather than the destination category,
# so both fall back to `note`. The five overlapping values pass
# through unchanged. `insight`, `workflow`, and `project` are not
# derivable from detection alone: `project` is set by the PARA-
# project override below, and `insight` plus `workflow` remain user-
# set at the skill orchestrator's confirmation step.
_DETECTION_TO_FRONTMATTER_TYPE: dict[ContentType, NoteType] = {
    "session": "session",
    "document": "note",
    "reference": "reference",
    "context": "context",
    "prompt": "prompt",
    "transcription": "note",
    "note": "note",
}


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
    note_type = _derive_note_type(detection, para)

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
        type=note_type,
        domain=classification.primary,
        tags=tags,
        notebook=notebook,
        source_id="",
        project=project,
    )


def _derive_note_type(detection: DetectionResult, para: ParaResult) -> NoteType:
    """Pick the fixed_domains frontmatter `type` field value.

    PARA-project overrides detection: when para categorizes a note as
    a project, the frontmatter type follows so routing and type stay
    consistent. Otherwise the detection type is translated through
    `_DETECTION_TO_FRONTMATTER_TYPE`.
    """
    if para.category == "project":
        return "project"
    return _DETECTION_TO_FRONTMATTER_TYPE[detection.type]


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
    sentences = _split_sentences(stripped)
    if not sentences:
        return stripped
    # Prefer the first sentence whose slug fits the cap naturally.
    # Avoids ugly truncation when the opening sentence runs long but a
    # later one is short and on-topic.
    for sentence in sentences:
        slug = _slug_normalize(sentence)
        if slug and len(slug) <= _TITLE_MAX_CHARS:
            return sentence
    return sentences[0]


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_BOUNDARY_PATTERN.split(text)
    return [p.strip() for p in parts if p.strip()]


def _slug_normalize(source: str) -> str:
    """Slug pipeline minus the cap. Used to size-test candidates before cutting."""
    if not source:
        return ""
    # NFKD-normalize and strip combining accents so Portuguese inputs
    # ("Reunião") become ASCII-safe filenames ("reuniao").
    normalized = unicodedata.normalize("NFKD", source)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def _slugify(source: str) -> str:
    slugged = _slug_normalize(source)
    if not slugged or len(slugged) <= _TITLE_MAX_CHARS:
        return slugged
    # Word-boundary cut: trim back to the last hyphen at or before the
    # cap so titles never end mid-word.
    cut = slugged[:_TITLE_MAX_CHARS]
    last_hyphen = cut.rfind("-")
    if last_hyphen < 0:
        # Single token longer than the cap has no boundary-safe trim;
        # return empty so _build_title falls back to the date-based default.
        return ""
    return cut[:last_hyphen].strip("-")


def _build_tags(classification: ClassificationResult) -> tuple[str, ...]:
    if classification.uncertain:
        return ()
    seeded = (classification.primary,) + classification.secondary
    return seeded[:_MAX_TAGS]
