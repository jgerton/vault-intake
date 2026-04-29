"""Step 2: refine.

Produces a readability-pass version of oral or brain-dump content
while keeping the verbatim original alongside. The skill orchestrator
decides whether to invoke this function based on
`Config.refinement_enabled` and `DetectionResult.refinement_applicable`;
this module assumes the call has already been gated.

Per build spec lines 70-84 the refined output:

- Removes filler ("e aí", "tipo", "aí", "né") only at word boundaries.
- Breaks into paragraphs at oral-monologue connective signals (e, aí,
  então, tipo) following sentence-end punctuation, with a soft cap of
  five sentences per paragraph.
- Preserves every idea from the original. No editorializing,
  summarizing, interpreting, no information added, no items removed
  because they seem off-topic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RefinedContent:
    refined: str
    original: str
    changed: bool


# Spec line 74 lists "e aí", "tipo", "né" as filler when pure noise.
# "aí" alone is also pure-noise filler in oral Portuguese; the
# multiword "e aí" is processed first so the connective "e" is not
# stranded.
_MULTIWORD_FILLERS: tuple[str, ...] = ("e aí",)
_SINGLE_WORD_FILLERS: tuple[str, ...] = ("tipo", "aí", "né")

# Connectives that signal a new paragraph in oral monologue (spec line
# 64). Used only as paragraph-boundary markers; "tipo" and "aí" are
# also fillers and will be stripped after segmentation runs.
_PARAGRAPH_CONNECTIVES: frozenset[str] = frozenset(
    {"e", "aí", "então", "tipo"}
)

# Soft cap: when no connective signal fires, force a paragraph break
# after this many sentences so wall-of-text monologues still segment.
_MAX_SENTENCES_PER_PARAGRAPH = 5

_PARAGRAPH_SPLIT_PATTERN = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
_MULTIPLE_SPACES_PATTERN = re.compile(r" {2,}")
_SPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(r"\s+([,.!?;:])")
_REPEATED_COMMAS_PATTERN = re.compile(r",{2,}")
_EMPTY_PARENS_PATTERN = re.compile(r"\(\s*\)")
_LEADING_PUNCTUATION_PATTERN = re.compile(r"^[\s,;:]+")


def refine(text: str) -> RefinedContent:
    """Produce a readability-pass refinement of `text`.

    Returns a `RefinedContent` carrying the refined version, the
    verbatim original (never edited), and a `changed` flag indicating
    whether refinement altered the input.
    """
    if not text or not text.strip():
        return RefinedContent(refined=text, original=text, changed=False)

    paragraphs = _segment_into_paragraphs(text)
    refined_paragraphs = [_remove_fillers(p) for p in paragraphs]
    refined_paragraphs = [p for p in refined_paragraphs if p]
    refined = "\n\n".join(refined_paragraphs)
    return RefinedContent(
        refined=refined,
        original=text,
        changed=refined != text,
    )


def _segment_into_paragraphs(text: str) -> list[str]:
    """Group the input's sentences into paragraphs.

    Pre-existing `\\n\\n` paragraph breaks are preserved. Within each
    pre-existing paragraph, a new break starts when the next sentence
    opens with an oral-monologue connective, or when the soft cap is
    reached.
    """
    pre_paragraphs = _PARAGRAPH_SPLIT_PATTERN.split(text)
    out: list[str] = []
    for pre in pre_paragraphs:
        if not pre.strip():
            continue
        out.extend(_walk_sentences(pre))
    return out


def _walk_sentences(paragraph_text: str) -> list[str]:
    sentences = [
        s for s in _SENTENCE_SPLIT_PATTERN.split(paragraph_text.strip()) if s
    ]
    if not sentences:
        return []

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for sentence in sentences:
        first_word = _first_word_lower(sentence)
        starts_new_paragraph = bool(current) and (
            first_word in _PARAGRAPH_CONNECTIVES
            or len(current) >= _MAX_SENTENCES_PER_PARAGRAPH
        )
        if starts_new_paragraph:
            paragraphs.append(current)
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        paragraphs.append(current)
    return [" ".join(p) for p in paragraphs]


def _first_word_lower(sentence: str) -> str:
    stripped = sentence.lstrip()
    if not stripped:
        return ""
    return stripped.split(maxsplit=1)[0].lower()


def _remove_fillers(paragraph: str) -> str:
    out = paragraph
    # V1 deliberate choice: filler matching is case-insensitive. A
    # standalone capitalized "Tipo", "Aí", or "Né" is treated as filler
    # even when it might be a proper noun. Users recover any such usage
    # from the verbatim original block (`## Captura original`).
    for filler in _MULTIWORD_FILLERS:
        out = re.sub(
            rf"\b{re.escape(filler)}\b",
            " ",
            out,
            flags=re.IGNORECASE,
        )
    for filler in _SINGLE_WORD_FILLERS:
        out = re.sub(
            rf"\b{re.escape(filler)}\b",
            " ",
            out,
            flags=re.IGNORECASE,
        )
    out = _MULTIPLE_SPACES_PATTERN.sub(" ", out)
    out = _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", out)
    out = _REPEATED_COMMAS_PATTERN.sub(",", out)
    out = _EMPTY_PARENS_PATTERN.sub("", out)
    out = _MULTIPLE_SPACES_PATTERN.sub(" ", out)
    out = _LEADING_PUNCTUATION_PATTERN.sub("", out)
    return out.strip()
