"""Tests for Step 2: refine.

Per build spec lines 70-84: produce a readability-pass version of
oral or brain-dump content while preserving the verbatim original.

Six non-negotiable safety rules apply: no editorializing, no
summarizing, no interpreting, no information added, no items removed
because they seem off-topic, never edit the original.

Refinement is rule-based v1 (kickoff Option A): light filler removal
plus conservative paragraph segmentation with an N=5 sentence soft
cap. The skill orchestrator decides whether to invoke `refine()`
based on `Config.refinement_enabled` and
`DetectionResult.refinement_applicable`; this module assumes its
caller has already gated the call.
"""
from __future__ import annotations

import dataclasses

import pytest

from vault_intake.refine import RefinedContent, refine


# ---------------------------------------------------------------------------
# Round 1: filler removal isolation (only at word boundaries)
# ---------------------------------------------------------------------------


def test_removes_standalone_tipo_filler():
    result = refine("isso tipo é legal")

    assert "tipo" not in result.refined.split()
    assert "isso" in result.refined
    assert "legal" in result.refined


def test_preserves_typico_containing_tipo_substring():
    result = refine("isso é típico do projeto")

    assert "típico" in result.refined


def test_preserves_pais_containing_ai_substring():
    result = refine("vou pro país no fim do ano")

    assert "país" in result.refined


def test_removes_standalone_ne_filler():
    result = refine("é complicado né, mas vamos seguir")

    assert "complicado" in result.refined
    assert "vamos" in result.refined
    assert "seguir" in result.refined
    assert " né " not in result.refined
    assert " né," not in result.refined


def test_removes_multiword_e_ai_filler():
    result = refine("fiz isso e aí parei pra pensar")

    assert "fiz" in result.refined
    assert "parei" in result.refined
    assert "pensar" in result.refined
    assert "e aí" not in result.refined.lower()


def test_filler_removal_keeps_content_words():
    text = "tipo a estratégia tipo do produto né tipo funcionou bem"

    result = refine(text)

    for word in ("estratégia", "produto", "funcionou"):
        assert word in result.refined


# ---------------------------------------------------------------------------
# Round 2: conservative paragraph segmentation
# ---------------------------------------------------------------------------


def test_short_input_stays_single_paragraph():
    text = "Implementei o módulo de detecção. Funciona bem."

    result = refine(text)

    assert "\n\n" not in result.refined


def test_segments_at_sentence_end_followed_by_oral_connective():
    text = (
        "Implementei o módulo de detecção. então comecei pela parte de "
        "configuração. Funciona bem."
    )

    result = refine(text)

    assert result.refined.count("\n\n") == 1


def test_soft_cap_splits_after_five_sentences():
    text = " ".join(f"Frase número {i}." for i in range(1, 8))

    result = refine(text)

    paragraphs = result.refined.split("\n\n")
    assert len(paragraphs) == 2
    assert paragraphs[0].count(".") == 5
    assert paragraphs[1].count(".") == 2


def test_preserves_existing_paragraph_breaks():
    text = "Primeiro parágrafo curto.\n\nSegundo parágrafo curto."

    result = refine(text)

    assert result.refined.count("\n\n") >= 1


def test_strips_filler_at_paragraph_start():
    text = "Primeira ideia importante. tipo segunda ideia também importante."

    result = refine(text)

    paragraphs = result.refined.split("\n\n")
    assert len(paragraphs) == 2
    assert "tipo" not in paragraphs[1].split()
    assert "segunda" in paragraphs[1]


# ---------------------------------------------------------------------------
# Round 3: idempotence
# ---------------------------------------------------------------------------


def test_refining_already_refined_text_is_no_op():
    text = (
        "Implementei o módulo, tipo, primeiro pela parte de configuração. "
        "Funcionou bem nas primeiras chamadas. então decidi escrever os "
        "testes logo. Cobri os casos principais né. Senti que faltava algo. "
        "Voltei pro spec pra revisar."
    )

    once = refine(text).refined
    twice = refine(once).refined

    assert twice == once


# ---------------------------------------------------------------------------
# Round 4: preservation invariant (no information removed)
# ---------------------------------------------------------------------------


def test_preserves_all_content_words_from_original():
    text = (
        "tipo fizemos a estratégia do produto né e aí o resultado "
        "apareceu rápido."
    )

    result = refine(text)

    for word in ("fizemos", "estratégia", "produto", "resultado", "apareceu"):
        assert word in result.refined


def test_original_field_is_verbatim_input():
    text = "  tipo, exatamente assim né.  "

    result = refine(text)

    assert result.original == text


def test_refined_never_adds_words():
    text = "tipo a estratégia do produto né funcionou rápido demais."

    result = refine(text)

    original_word_count = len(text.split())
    refined_word_count = len(result.refined.split())
    assert refined_word_count <= original_word_count


# ---------------------------------------------------------------------------
# Round 5: changed flag
# ---------------------------------------------------------------------------


def test_changed_flag_true_when_filler_removed():
    result = refine("isso tipo funciona")

    assert result.changed is True


def test_changed_flag_false_when_text_unchanged():
    text = "Texto curto sem filler nem segmentação extra."

    result = refine(text)

    assert result.changed is False


def test_changed_flag_true_when_segmentation_added():
    text = " ".join(f"Frase {i}." for i in range(1, 8))

    result = refine(text)

    assert result.changed is True


# ---------------------------------------------------------------------------
# Round 6: empty / whitespace-only input
# ---------------------------------------------------------------------------


def test_empty_string_returns_unchanged():
    result = refine("")

    assert result.refined == ""
    assert result.original == ""
    assert result.changed is False


def test_whitespace_only_returns_unchanged():
    text = "   \n  "

    result = refine(text)

    assert result.changed is False
    assert result.original == text


# ---------------------------------------------------------------------------
# Round 7: dataclass shape
# ---------------------------------------------------------------------------


def test_refined_content_is_frozen():
    result = refine("test")

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.refined = "modified"  # type: ignore[misc]


def test_refined_content_has_required_fields():
    result = refine("test")

    assert isinstance(result, RefinedContent)
    assert isinstance(result.refined, str)
    assert isinstance(result.original, str)
    assert isinstance(result.changed, bool)
