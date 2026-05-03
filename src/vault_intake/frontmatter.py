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

# Filler/discourse words that should not become note titles. Keyed by
# language code. When a title candidate starts with one of these words
# the heuristic skips to the next candidate sentence.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"ok", "so", "well", "right", "yeah", "yes", "no"}),
    "pt-BR": frozenset({
        "certo", "ok", "entao", "ne", "bom", "bem", "enfim", "tipo",
        "assim", "olha", "veja", "cara", "ei", "ah", "oh", "então",
        "né", "oi", "ola", "hey",
    }),
    "pt": frozenset({
        "certo", "ok", "entao", "ne", "bom", "bem", "enfim", "tipo",
        "assim", "olha", "veja", "cara", "ei", "ah", "oh", "então",
        "né", "oi", "ola", "hey",
    }),
}


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

    # Track-specific fields. In fixed_domains mode: `domain` and `project`
    # are populated; `theme` is empty. In emergent mode: `theme` is
    # populated; `domain` and `project` are empty. `to_yaml()` emits the
    # right shape based on which is set. `type` is a closed enum in
    # fixed_domains; open (any string) in emergent.
    type: str
    domain: str
    theme: str
    tags: tuple[str, ...]
    notebook: str
    source_id: str
    project: str

    def to_yaml(self) -> str:
        """Emit YAML-frontmatter-ready text for the canonical fields.

        Field order matches the architecture plan baseline (OS-wide
        first, then cross-track, then track-specific) so diffs stay
        readable. None confidence emits as an empty string per the
        baseline's "optional" rule. When `theme` is set (emergent mode),
        emits `theme` instead of `domain` and omits `project`.
        """
        base: list[tuple[str, object]] = [
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
        ]
        if self.domain:
            # fixed_domains shape
            track: list[tuple[str, object]] = [
                ("domain", self.domain),
                ("tags", list(self.tags)),
                ("notebook", self.notebook),
                ("source_id", self.source_id),
                ("project", self.project),
            ]
        else:
            # emergent shape: theme replaces domain; project omitted
            track = [
                ("theme", self.theme),
                ("tags", list(self.tags)),
                ("notebook", self.notebook),
                ("source_id", self.source_id),
            ]
        return yaml.safe_dump(
            dict(base + track),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def generate_frontmatter(
    text: str,
    detection: DetectionResult,
    refinement: RefinedContent | None,
    classification: ClassificationResult,
    para: ParaResult | None,
    config: Config,
    *,
    source_type: SourceType = "paste",
    source_uri: str = "",
    captured_at: str | None = None,
) -> Frontmatter:
    captured = captured_at or date.today().isoformat()
    note_date = captured.split("T", 1)[0]

    is_braindump = detection.type == "note" and detection.refinement_applicable
    original_ref = (
        _ORIGINAL_REF_MARKER if refinement is not None and refinement.changed else ""
    )
    tags = _build_tags(classification)

    if config.mode == "emergent":
        domain_or_theme = (
            classification.primary
            if classification.primary and not classification.uncertain
            else ""
        )
        title = _build_title(
            text,
            fallback_date=note_date,
            language=config.language,
            is_braindump=is_braindump,
            domain_or_theme=domain_or_theme,
        )
        notebook = config.notebook_map.get(classification.primary, "")
        note_type = _DETECTION_TO_FRONTMATTER_TYPE[detection.type]
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
            domain="",
            theme=classification.primary,
            tags=tags,
            notebook=notebook,
            source_id="",
            project="",
        )

    # fixed_domains path
    assert para is not None, "para must be provided for fixed_domains mode"
    domain_or_theme = (
        classification.primary
        if classification.primary and not classification.uncertain
        else ""
    )
    title = _build_title(
        text,
        fallback_date=note_date,
        language=config.language,
        is_braindump=is_braindump,
        domain_or_theme=domain_or_theme,
    )
    notebook = config.notebook_map.get(classification.primary, "")
    project = para.project_slug or "" if para.category == "project" else ""
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
        theme="",
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


def _build_title(
    text: str,
    *,
    fallback_date: str,
    language: str = "en",
    is_braindump: bool = False,
    domain_or_theme: str = "",
) -> str:
    stopwords = _STOPWORDS.get(language, _STOPWORDS["en"])
    if is_braindump:
        if domain_or_theme:
            return f"braindump-{domain_or_theme}-{fallback_date}"
        source = _extract_title_source(text, stopwords=stopwords)
        slug = _slugify(source)
        if slug:
            return f"braindump-{slug}-{fallback_date}"
        return f"braindump-{fallback_date}"
    source = _extract_title_source(text, stopwords=stopwords)
    slug = _slugify(source)
    if not slug:
        return f"note-{fallback_date}"
    return slug


def _extract_title_source(text: str, *, stopwords: frozenset[str] = frozenset()) -> str:
    match = _H1_PATTERN.search(text)
    if match:
        candidate = match.group(1)
        if not _is_filler(candidate, stopwords):
            return candidate
    stripped = text.strip()
    if not stripped:
        return ""
    sentences = _split_sentences(stripped)
    if not sentences:
        return stripped
    # Prefer the first sentence whose slug fits the cap naturally and is
    # not a filler word. Avoids ugly truncation and filler-as-title names.
    for sentence in sentences:
        if _is_filler(sentence, stopwords):
            continue
        slug = _slug_normalize(sentence)
        if slug and len(slug) <= _TITLE_MAX_CHARS:
            return sentence
    # No short-enough non-filler sentence; fall back to first non-filler
    # or first sentence if all are filler.
    for sentence in sentences:
        if not _is_filler(sentence, stopwords):
            return sentence
    return sentences[0]


def _is_filler(text: str, stopwords: frozenset[str]) -> bool:
    """Return True when `text` consists entirely of stopwords or starts with one.

    Normalizes to lowercase ASCII so accented variants ("entao" matches
    "então") and mixed-case inputs are handled consistently.
    """
    if not stopwords or not text.strip():
        return False
    normalized = unicodedata.normalize("NFKD", text.strip())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    first_word = re.split(r"[^a-z0-9]+", ascii_text)[0]
    return first_word in stopwords


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
