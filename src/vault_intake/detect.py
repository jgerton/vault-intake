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
_USER_SIDE_LABELS: frozenset[str] = frozenset({"user", "human"})
_ASSISTANT_SIDE_LABELS: frozenset[str] = frozenset({"assistant", "ai", "chatgpt"})

_MARKDOWN_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_SETEXT_HEADING_PATTERN = re.compile(
    r"^[^\n]+\n[=\-]{2,}\s*$",
    re.MULTILINE,
)
_URL_PATTERN = re.compile(r"https?://\S+")

# Types that already have explicit structure; they should not be sent
# through Step 2 (Refine) even when long and unstructured-looking.
_ALREADY_STRUCTURED: frozenset[ContentType] = frozenset(
    {"session", "document", "reference"}
)

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
# in this tuple wins. session leads because user/assistant turn markers
# are the strictest structural signal (rare false positives) and Step 2
# refinement would corrupt their structure.
_TYPE_PRIORITY: tuple[ContentType, ...] = (
    "session",
    "transcription",
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
    has_setext_headings = bool(_SETEXT_HEADING_PATTERN.search(text))
    has_structural_headings = has_markdown_headings or has_setext_headings
    has_connectives = any(c in lower for c in _TRANSCRIPTION_CONNECTIVES)

    signal_map: dict[ContentType, list[str]] = {
        t: [] for t in _TYPE_PRIORITY
    }

    if _is_dialogue(text):
        signal_map["session"].append("user_assistant_turns")

    if has_markdown_headings:
        signal_map["document"].append("markdown_headings")
    if has_setext_headings:
        signal_map["document"].append("setext_headings")

    if _URL_PATTERN.search(text):
        signal_map["reference"].append("url_present")

    if any(phrase in lower for phrase in _CONTEXT_PHRASES):
        signal_map["context"].append("first_person_decision_phrasing")

    if any(phrase in lower for phrase in _PROMPT_PHRASES):
        signal_map["prompt"].append("prompt_directive_phrasing")

    if (
        word_count > TRANSCRIPTION_MIN_WORDS
        and not has_structural_headings
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
            refinement_applicable=_is_brain_dump(
                word_count=word_count,
                has_structural_headings=has_structural_headings,
            ),
        )

    winner = detected_types[0]
    uncertain = len(detected_types) > 1

    refinement_applicable = _refinement_applies(
        winner=winner,
        word_count=word_count,
        has_structural_headings=has_structural_headings,
    )

    return DetectionResult(
        type=winner,
        uncertain=uncertain,
        signals=tuple(signal_map[winner]),
        refinement_applicable=refinement_applicable,
    )


def _is_dialogue(text: str) -> bool:
    """Detect a real conversational structure.

    Spec line 59 calls for "user/assistant turns," not a single
    isolated label. Require at least one user-side and one
    assistant-side turn marker so a stray quoted `User:` line in prose
    does not get classified as a session.
    """
    labels: set[str] = set()
    for match in _SESSION_TURN_PATTERN.finditer(text):
        labels.add(match.group(1).lower())
    return bool(labels & _USER_SIDE_LABELS) and bool(
        labels & _ASSISTANT_SIDE_LABELS
    )


def _is_brain_dump(*, word_count: int, has_structural_headings: bool) -> bool:
    """Brain-dump heuristic gating Step 2 (Refine).

    Long unstructured prose benefits from a readability pass; short
    notes, reminders, and well-structured snippets skip Step 2.
    """
    return (
        word_count >= BRAIN_DUMP_MIN_WORDS
        and not has_structural_headings
    )


def _refinement_applies(
    *,
    winner: ContentType,
    word_count: int,
    has_structural_headings: bool,
) -> bool:
    """Whether Step 2 (Refine) should run for this detection result.

    Always True for transcriptions per spec line 69. For non-already-
    structured types (note, context, prompt), True when the input
    looks like a brain dump (long and unstructured). Always False for
    already-structured types (session, document, reference) so Step 2
    cannot corrupt turn structure, headings, or external content.
    """
    if winner == "transcription":
        return True
    if winner in _ALREADY_STRUCTURED:
        return False
    return _is_brain_dump(
        word_count=word_count,
        has_structural_headings=has_structural_headings,
    )
