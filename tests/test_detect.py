"""Tests for Step 1: detect content type.

Per build spec lines 56-68: classify input as one of seven closed-enum
content types based on signal patterns. Surface uncertainty when signals
overlap so the skill can ask a single confirmation question.
"""
from __future__ import annotations

import pytest

from vault_intake.detect import ContentType, DetectionResult, detect_content_type


# ---------------------------------------------------------------------------
# Round 1: clear-signal cases (one per type) plus default and refinement flag
# ---------------------------------------------------------------------------


def test_detects_session_from_user_assistant_turns():
    text = (
        "User: What's the difference between mocks and stubs?\n"
        "Assistant: Mocks record interactions; stubs return canned values.\n"
        "User: Got it, thanks.\n"
        "Assistant: You're welcome."
    )

    result = detect_content_type(text)

    assert result.type == "session"


def test_detects_document_from_markdown_headings():
    text = (
        "# Project Plan\n\n"
        "## Overview\n\n"
        "The plan covers four phases.\n\n"
        "## Timeline\n\n"
        "Phase 1 starts Monday.\n\n"
        "## Risks\n\n"
        "Schedule slip is the main risk.\n"
    )

    result = detect_content_type(text)

    assert result.type == "document"


def test_detects_reference_from_url_signal():
    text = (
        "Excerpt from https://example.com/article by Jane Doe (2025).\n\n"
        "The author argues that retrieval-augmented generation is "
        "underexplored in agentic systems."
    )

    result = detect_content_type(text)

    assert result.type == "reference"


def test_detects_context_from_first_person_decisions():
    text = (
        "I decided to keep the YCAH freemium tier capped at 100 members. "
        "My position is that gated growth produces stronger community "
        "engagement than open enrollment."
    )

    result = detect_content_type(text)

    assert result.type == "context"


def test_detects_prompt_from_send_this_phrasing():
    text = (
        "Send this to ChatGPT: You are a senior copyeditor. Review the "
        "draft below and surface every passive-voice sentence. Use this "
        "with the latest GPT-5.4 model."
    )

    result = detect_content_type(text)

    assert result.type == "prompt"


def test_detects_transcription_from_long_unstructured_speech():
    sentence = (
        "Então tipo eu queria falar sobre o projeto da intake skill, "
        "aí pensei que talvez a gente precise repensar como o detect "
        "funciona, e tipo o problema é que o spec diz uma coisa mas a "
        "implementação anterior fazia outra, então acho que o melhor "
        "caminho é seguir o spec mesmo. "
    )
    text = sentence * 8  # well over 300 words, no markdown, repeated ideas

    result = detect_content_type(text)

    assert result.type == "transcription"


def test_defaults_to_note_for_short_generic_text():
    text = "Reminder to email Sarah about the Q3 budget review."

    result = detect_content_type(text)

    assert result.type == "note"


def test_refinement_applicable_for_transcription():
    sentence = (
        "Então tipo o ponto é que a gente tem que decidir se a refine "
        "roda automatico ou se pergunta antes, aí eu acho que pergunta "
        "antes e tipo deixa o usuario aprovar. "
    )
    text = sentence * 8

    result = detect_content_type(text)

    assert result.refinement_applicable is True


def test_refinement_not_applicable_for_document():
    text = (
        "# Spec\n\n"
        "## Overview\n\n"
        "Structured content does not need refinement.\n"
    )

    result = detect_content_type(text)

    assert result.refinement_applicable is False


def test_handles_empty_string_as_note_default():
    result = detect_content_type("")

    assert result.type == "note"


# ---------------------------------------------------------------------------
# Round 2: overlap / uncertain flag and signals exposure
# ---------------------------------------------------------------------------


def test_uncertain_false_when_clear_dominant_signal():
    text = (
        "# Project Plan\n\n"
        "## Overview\n\n"
        "The plan covers four phases.\n"
    )

    result = detect_content_type(text)

    assert result.uncertain is False


def test_flags_uncertain_on_session_plus_reference_overlap():
    text = (
        "User: Can you summarize https://example.com/article by Jane Doe?\n"
        "Assistant: The article from https://example.com/article argues "
        "that RAG is underexplored.\n"
        "User: Thanks."
    )

    result = detect_content_type(text)

    assert result.uncertain is True


def test_flags_uncertain_on_context_plus_prompt_overlap():
    text = (
        "I decided we should use a stricter copy review pass. "
        "Send this to ChatGPT: review every draft for passive voice. "
        "My position is the editor agent runs after every blog draft."
    )

    result = detect_content_type(text)

    assert result.uncertain is True


def test_flags_uncertain_on_transcription_plus_context_overlap():
    sentence = (
        "Então tipo I decided the freemium tier should cap at 100 "
        "members, aí my position is that gated growth yields better "
        "engagement than open enrollment, e tipo we need to test this "
        "claim before we commit to the cap as a hard policy. "
    )
    text = sentence * 10

    result = detect_content_type(text)

    assert result.uncertain is True


def test_signals_field_exposes_detected_hints():
    text = (
        "# Heading\n\n"
        "User: question\n"
        "Assistant: answer\n"
    )

    result = detect_content_type(text)

    # at least one signal name surfaces for the dominant type
    assert len(result.signals) >= 1
    assert all(isinstance(s, str) for s in result.signals)


# ---------------------------------------------------------------------------
# Round 3: boundary cases
# ---------------------------------------------------------------------------


def test_short_unstructured_speech_is_note_not_transcription():
    text = (
        "Então tipo o ponto e que a gente tem que decidir, ai eu acho "
        "que pergunta antes."
    )

    result = detect_content_type(text)

    assert result.type == "note"


def test_refinement_applicable_for_unstructured_brain_dump_note():
    text = (
        "ok then maybe rewrite the intro and reorder the sections "
        "actually no the timeline goes first then the risks then "
        "overview no wait keep overview first but trim it"
    )

    result = detect_content_type(text)

    assert result.refinement_applicable is True


def test_detection_result_is_frozen_dataclass():
    result = detect_content_type("test")

    with pytest.raises((AttributeError, TypeError)):
        result.type = "session"  # type: ignore[misc]


def test_content_type_literal_covers_seven_values():
    valid: set[ContentType] = {
        "session",
        "document",
        "reference",
        "context",
        "prompt",
        "transcription",
        "note",
    }
    result = detect_content_type("anything")

    assert result.type in valid


# ---------------------------------------------------------------------------
# Round 4: Codex review follow-ups (session priority, false positives,
# setext headings, brain-dump gating, more overlap pairs, word boundary)
# ---------------------------------------------------------------------------


def test_session_beats_transcription_on_long_chat_with_connectives():
    """Per project decision 2026-04-29: session > transcription.

    A long Claude.ai-style chat with informal Portuguese filler must
    classify as session, not transcription, so Step 2 refinement does
    not corrupt turn structure.
    """
    turn = (
        "User: Então tipo eu queria entender como o detect funciona, e "
        "tipo o que acontece se o input for muito longo aí com vários "
        "sinais misturados, e tipo qual a prioridade entre eles.\n"
        "Assistant: Tipo, a prioridade é configurada no priority tuple "
        "e tipo o session vence o transcription porque os turn markers "
        "são um sinal estrutural mais forte aí, e tipo o transcription "
        "fica como fallback pra texto sem turns.\n"
    )
    text = turn * 6  # >300 words, dual-side turns, connectives, no markdown

    result = detect_content_type(text)

    assert result.type == "session"


def test_does_not_classify_single_isolated_user_label_as_session():
    text = "User: Reminder to ping Bob about the build status."

    result = detect_content_type(text)

    assert result.type != "session"


def test_session_requires_both_user_and_assistant_markers():
    text = (
        "User: first item\n"
        "User: second item\n"
        "User: third item\n"
    )

    result = detect_content_type(text)

    assert result.type != "session"


def test_detects_document_from_setext_headings():
    text = (
        "Project Plan\n"
        "============\n\n"
        "The plan covers four phases.\n\n"
        "Timeline\n"
        "--------\n\n"
        "Phase 1 starts Monday.\n"
    )

    result = detect_content_type(text)

    assert result.type == "document"


def test_refinement_applicable_for_long_unstructured_context_dump():
    """Brain-dump gating runs independently of winner when the winning
    type is not already structured (session/document/reference).
    """
    text = (
        "I decided the plan is broken. My position is that we should "
        "rewrite the spec from scratch. for client X we do Y but the "
        "Y is no longer current and tipo nobody updated the doc, so I "
        "decided it's worth tearing it down and rebuilding it. "
    ) * 5  # context type, long, no markdown headings

    result = detect_content_type(text)

    assert result.type == "context"
    assert result.refinement_applicable is True


def test_transcription_threshold_triggers_above_300_words():
    sentence = "tipo "
    text = sentence * 301  # 301 tokens; >300 satisfied

    result = detect_content_type(text)

    assert result.type == "transcription"


def test_transcription_threshold_does_not_trigger_at_exactly_300_words():
    sentence = "tipo "
    text = sentence * 300  # 300 tokens; >300 not satisfied

    result = detect_content_type(text)

    assert result.type != "transcription"


def test_flags_uncertain_on_document_plus_reference_overlap():
    text = (
        "# Spec\n\n"
        "## Overview\n\n"
        "See https://example.com/standard for the upstream definition.\n\n"
        "## Notes\n\n"
        "Author Jane Doe documented the constraints in 2025.\n"
    )

    result = detect_content_type(text)

    assert result.uncertain is True


def test_flags_uncertain_on_document_plus_context_overlap():
    text = (
        "# Decision Log\n\n"
        "## Background\n\n"
        "I decided to keep the freemium tier capped at 100 members.\n\n"
        "## Rationale\n\n"
        "My position is that gated growth produces stronger engagement.\n"
    )

    result = detect_content_type(text)

    assert result.uncertain is True


def test_flags_uncertain_on_prompt_plus_reference_overlap():
    text = (
        "Send this to ChatGPT: review the article at "
        "https://example.com/article by Jane Doe and surface every "
        "claim that lacks a citation. Use this with the latest model."
    )

    result = detect_content_type(text)

    assert result.uncertain is True
