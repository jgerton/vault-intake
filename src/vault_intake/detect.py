"""Step 1: detect content type.

Classifies input text into one of seven closed-enum content types per
build spec lines 56-68. When signals from more than one type fire, the
dominant type is picked and `uncertain=True` is set so the skill layer
can ask a single confirmation question (consolidated safety rule 2).

The result also exposes `refinement_applicable`, which the skill uses
to decide whether Step 2 (Refine) runs. Refinement applies to
transcriptions and to unstructured brain-dump notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ContentType = Literal[
    "session",
    "document",
    "reference",
    "context",
    "prompt",
    "transcription",
    "note",
]


@dataclass(frozen=True)
class DetectionResult:
    type: ContentType
    uncertain: bool
    signals: tuple[str, ...]
    refinement_applicable: bool


# Spec line 64: transcription length threshold ">300 words".
TRANSCRIPTION_MIN_WORDS = 300

# Brain-dump note threshold: long enough to be worth refining, short
# enough that it would not have hit the transcription path.
BRAIN_DUMP_MIN_WORDS = 20

_SESSION_TURN_PATTERN = re.compile(
    r"^\s*(User|Assistant|Human|AI|ChatGPT)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_URL_PATTERN = re.compile(r"https?://\S+")

_CONTEXT_PHRASES: tuple[str, ...] = (
    "i decided",
    "my position is",
    "for client",
)
_PROMPT_PHRASES: tuple[str, ...] = (
    "send this to",
    "prompt for",
    "use this with",
)
_TRANSCRIPTION_CONNECTIVES: tuple[str, ...] = (
    " e ",
    " aí ",
    " então ",
    " tipo ",
)

# When more than one type's signals fire, the type appearing earliest
# in this tuple wins. Strictest signals first.
_TYPE_PRIORITY: tuple[ContentType, ...] = (
    "transcription",
    "session",
    "prompt",
    "context",
    "document",
    "reference",
)


def detect_content_type(text: str) -> DetectionResult:
    """Classify `text` into one of the seven content types.

    Per spec line 67: when signals overlap, picks the most likely type
    and flags uncertainty so the caller can ask a single confirmation
    question. Empty or signal-free input falls through to `note`.
    """
    lower = text.lower()
    word_count = len(text.split())
    has_markdown_headings = bool(_MARKDOWN_HEADING_PATTERN.search(text))
    has_connectives = any(c in lower for c in _TRANSCRIPTION_CONNECTIVES)

    signal_map: dict[ContentType, list[str]] = {
        t: [] for t in _TYPE_PRIORITY
    }

    if _SESSION_TURN_PATTERN.search(text):
        signal_map["session"].append("user_assistant_turns")

    if has_markdown_headings:
        signal_map["document"].append("markdown_headings")

    if _URL_PATTERN.search(text):
        signal_map["reference"].append("url_present")

    if any(phrase in lower for phrase in _CONTEXT_PHRASES):
        signal_map["context"].append("first_person_decision_phrasing")

    if any(phrase in lower for phrase in _PROMPT_PHRASES):
        signal_map["prompt"].append("prompt_directive_phrasing")

    if (
        word_count > TRANSCRIPTION_MIN_WORDS
        and not has_markdown_headings
        and has_connectives
    ):
        signal_map["transcription"].append("long_unstructured_text")
        signal_map["transcription"].append("informal_connectives")

    detected_types = [t for t in _TYPE_PRIORITY if signal_map[t]]

    if not detected_types:
        return DetectionResult(
            type="note",
            uncertain=False,
            signals=(),
            refinement_applicable=_is_brain_dump_note(
                word_count=word_count,
                has_markdown_headings=has_markdown_headings,
            ),
        )

    winner = detected_types[0]
    uncertain = len(detected_types) > 1

    if winner == "transcription":
        refinement_applicable = True
    else:
        refinement_applicable = False

    return DetectionResult(
        type=winner,
        uncertain=uncertain,
        signals=tuple(signal_map[winner]),
        refinement_applicable=refinement_applicable,
    )


def _is_brain_dump_note(*, word_count: int, has_markdown_headings: bool) -> bool:
    """Brain-dump note heuristic for `refinement_applicable`.

    A note is treated as a brain dump (worth refining) when it is long
    enough to benefit from a readability pass and lacks any structural
    scaffolding. Short notes, reminders, and well-structured snippets
    skip Step 2.
    """
    return word_count >= BRAIN_DUMP_MIN_WORDS and not has_markdown_headings
