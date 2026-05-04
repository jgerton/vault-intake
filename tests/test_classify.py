"""Tests for Step 3: classify (mode-dependent).

Covers fixed_domains mode end-to-end. Emergent mode raises NotImplementedError
in v1; emergent track lands in a separate session.
"""
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.classify import ClassificationResult, _tokenize, classify
from vault_intake.config import Config, ConfigError, Domain


def _make_config(
    *,
    mode: str = "fixed_domains",
    domains: tuple[Domain, ...] = (),
    threshold: float = 0.6,
) -> Config:
    return Config(
        vault_path=Path("/tmp/vault"),
        mode=mode,  # type: ignore[arg-type]
        domains=domains,
        notebook_map=MappingProxyType({}),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=True,
        classification_confidence_threshold=threshold,
    )


def test_classifies_clear_signal_with_high_confidence():
    config = _make_config(
        domains=(
            Domain(slug="ops", description="Operations, processes, and workflow automation."),
            Domain(slug="branding", description="Brand identity, logo design, and visual marketing materials."),
        ),
    )

    result = classify(
        "I am working on the new logo design and brand identity guide for the launch.",
        config,
    )

    assert isinstance(result, ClassificationResult)
    assert result.primary == "branding"
    assert result.mode == "fixed_domains"
    assert result.uncertain is False
    assert result.confidence >= config.classification_confidence_threshold
    assert result.secondary == ()


def test_low_confidence_below_threshold_marks_uncertain():
    config = _make_config(
        domains=(
            Domain(slug="design", description="Visual design and layout."),
            Domain(slug="ops", description="Operations workflow."),
        ),
    )

    # Only one weak token matches any domain vocab; not enough evidence.
    text = "I had a workflow issue yesterday."

    result = classify(text, config)

    assert result.primary == "ops"
    assert result.confidence < config.classification_confidence_threshold
    assert result.uncertain is True


def test_multi_domain_overlap_includes_secondary():
    config = _make_config(
        domains=(
            Domain(slug="design", description="Visual layout, color, typography."),
            Domain(slug="marketing", description="Marketing campaigns, audience, messaging, conversion."),
        ),
    )

    text = "Refining the visual layout and color palette to improve audience messaging."

    result = classify(text, config)

    assert result.primary == "design"
    assert result.secondary == ("marketing",)
    assert result.uncertain is False


def test_slug_match_outweighs_description_only_match():
    config = _make_config(
        domains=(
            Domain(slug="design", description="Layout, palette, hierarchy."),
            Domain(slug="branding", description="Visual identity."),
        ),
    )

    # design description matches twice (layout, palette) but branding's slug
    # is mentioned literally; slug should outrank pure description hits.
    text = "Thinking about branding plus layout and palette tweaks."

    result = classify(text, config)

    assert result.primary == "branding"


def test_no_keyword_matches_returns_uncertain_with_first_domain_default():
    config = _make_config(
        domains=(
            Domain(slug="ops", description="Operations and processes."),
            Domain(slug="branding", description="Brand identity and design."),
        ),
    )

    result = classify("Quantum tarragon symphonics chronograph.", config)

    assert result.confidence == 0.0
    assert result.uncertain is True
    assert result.primary == "ops"
    assert result.secondary == ()


def test_empty_input_returns_uncertain_zero_confidence():
    config = _make_config(
        domains=(
            Domain(slug="ops", description="Operations and processes."),
            Domain(slug="branding", description="Brand identity and design."),
        ),
    )

    result = classify("", config)

    assert result.confidence == 0.0
    assert result.uncertain is True
    assert result.secondary == ()
    assert result.primary in ("ops", "branding")



def test_tokenizer_preserves_unicode_letters():
    tokens = _tokenize("Saúde, alimentação, exercício.")

    assert "saúde" in tokens
    assert "alimentação" in tokens
    assert "exercício" in tokens


def test_classify_raises_on_empty_domains_in_fixed_mode():
    config = _make_config(domains=())  # fixed_domains with no domains configured

    with pytest.raises(ConfigError, match=r"domains"):
        classify("any text here", config)


def test_threshold_read_from_config_changes_uncertain():
    domains = (
        Domain(slug="ops", description="Operations and processes."),
        Domain(slug="branding", description="Brand identity, logo design, and visual marketing materials."),
    )
    text = "I am working on the new logo design and brand identity guide for the launch."

    lenient = _make_config(domains=domains, threshold=0.6)
    strict = _make_config(domains=domains, threshold=0.95)

    lenient_result = classify(text, lenient)
    strict_result = classify(text, strict)

    assert lenient_result.confidence == strict_result.confidence
    assert lenient_result.uncertain is False
    assert strict_result.uncertain is True


# ---------------------------------------------------------------------------
# Item 2 (M2): emergent mode classification
# ---------------------------------------------------------------------------


def _make_emergent_config(vault_path: Path, *, threshold: float = 0.6) -> Config:
    return Config(
        vault_path=vault_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt-BR",
        skip_notebooklm=False,
        refinement_enabled=True,
        classification_confidence_threshold=threshold,
    )


def test_emergent_classify_no_longer_raises(tmp_path: Path) -> None:
    """emergent classify now returns a result instead of raising NotImplementedError."""
    config = _make_emergent_config(tmp_path)
    result = classify("Braindump sobre posicionamento.", config)
    assert isinstance(result, ClassificationResult)
    assert result.mode == "emergent"


def test_emergent_classify_mode_field_is_emergent(tmp_path: Path) -> None:
    config = _make_emergent_config(tmp_path)
    result = classify("Some content.", config)
    assert result.mode == "emergent"


def test_emergent_classify_empty_vault_returns_uncertain_proposed_theme(
    tmp_path: Path,
) -> None:
    """Empty vault: no candidates, proposes theme from most-frequent input token."""
    config = _make_emergent_config(tmp_path)
    result = classify(
        "posicionamento posicionamento marca estrategia.", config
    )
    assert result.uncertain is True
    assert result.confidence == 0.0
    assert result.primary == "posicionamento"


def test_emergent_classify_matches_existing_theme_folder(tmp_path: Path) -> None:
    """A top-level vault folder whose name matches input content becomes the theme."""
    (tmp_path / "posicionamento").mkdir()
    (tmp_path / "marca").mkdir()
    config = _make_emergent_config(tmp_path)
    # Only mentions posicionamento so it unambiguously wins over marca
    result = classify(
        "Quero falar sobre posicionamento no mercado digital.", config
    )
    assert result.primary == "posicionamento"
    assert result.uncertain is False


def test_emergent_classify_matches_frontmatter_theme(tmp_path: Path) -> None:
    """A theme name from existing note frontmatter is a valid candidate."""
    note = tmp_path / "nota.md"
    note.write_text("---\ntheme: posicionamento\n---\nConteudo.", encoding="utf-8")
    config = _make_emergent_config(tmp_path)
    result = classify(
        "Quero falar sobre posicionamento de marca no mercado.", config
    )
    assert result.primary == "posicionamento"


def test_emergent_classify_secondary_themes_included(tmp_path: Path) -> None:
    """Themes scoring >= 40% of primary are included as secondary."""
    (tmp_path / "posicionamento").mkdir()
    (tmp_path / "marca").mkdir()
    config = _make_emergent_config(tmp_path)
    result = classify(
        "posicionamento posicionamento marca marca marca estrategia.", config
    )
    assert result.primary in ("posicionamento", "marca")
    assert len(result.secondary) >= 1


def test_emergent_classify_system_folders_excluded(tmp_path: Path) -> None:
    """Underscore-prefixed and dot-prefixed folders are not treated as themes."""
    (tmp_path / "_inbox").mkdir()
    (tmp_path / "_sinteses").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "posicionamento").mkdir()
    config = _make_emergent_config(tmp_path)
    result = classify("Conteudo sobre posicionamento de marca.", config)
    assert "_inbox" not in (result.primary,) + result.secondary
    assert "_sinteses" not in (result.primary,) + result.secondary
    assert ".git" not in (result.primary,) + result.secondary
    assert result.primary == "posicionamento"


def test_emergent_classify_uncertain_when_confidence_below_threshold(
    tmp_path: Path,
) -> None:
    """Low-scoring match (one weak hit) sets uncertain=True with strict threshold."""
    (tmp_path / "posicionamento").mkdir()
    config = _make_emergent_config(tmp_path, threshold=0.9)
    # One mention of the theme slug produces confidence 3/5 = 0.6, below 0.9
    result = classify("posicionamento estrategia.", config)
    assert result.uncertain is True


def test_emergent_classify_proposed_theme_is_most_frequent_token(
    tmp_path: Path,
) -> None:
    """When no themes exist, proposed theme is the most frequent significant token."""
    config = _make_emergent_config(tmp_path)
    result = classify(
        "marca marca marca posicionamento estrategia.", config
    )
    assert result.primary == "marca"
    assert result.uncertain is True


def test_emergent_classify_deduplicates_folder_and_frontmatter_sources(
    tmp_path: Path,
) -> None:
    """Same theme from both a folder name and a frontmatter value is not double-counted."""
    (tmp_path / "posicionamento").mkdir()
    note = tmp_path / "nota.md"
    note.write_text("---\ntheme: posicionamento\n---\nCorpo.", encoding="utf-8")
    config = _make_emergent_config(tmp_path)
    result = classify("posicionamento de marca", config)
    assert result.primary == "posicionamento"
    # Should not appear twice in secondary
    assert result.secondary.count("posicionamento") == 0


def test_emergent_classify_handles_utf8_bom_in_frontmatter(tmp_path: Path) -> None:
    """Codex review C-1 (2026-05-02): markdown files saved with a UTF-8 BOM
    (common on Windows) must still surface their `theme` frontmatter value
    as a candidate. _collect_emergent_themes reads with utf-8-sig; this
    test pins that codec choice."""
    note = tmp_path / "nota.md"
    note.write_bytes(
        "﻿---\ntheme: marca\n---\nCorpo.".encode("utf-8")
    )
    config = _make_emergent_config(tmp_path)
    result = classify("Quero falar sobre marca no mercado.", config)
    assert result.primary == "marca"


def test_emergent_proposed_theme_must_meet_min_word_length(tmp_path: Path) -> None:
    """Elio feedback 2026-05-04: emergent themes need stronger thresholds.
    Short tokens (< 4 chars) should not become proposed themes even if
    they're the most frequent significant token, because short tokens
    are usually noise (slang, fragments, ambiguous words)."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt-BR",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    # "vai" is 3 chars and appears 3 times; "carreira" is 8 chars and appears 2 times.
    # Without min-length filter, "vai" wins. With it, carreira wins.
    # ("vai" is also in pt-BR stopwords; pick a non-stopword 3-char token.)
    # "fim" (3 chars, "end" in pt) is not a stopword and appears 3 times.
    text = "fim fim fim carreira carreira"
    result = classify(text, config)
    assert result.primary == "carreira"


def test_emergent_proposes_nothing_when_no_token_meets_min_frequency(
    tmp_path: Path,
) -> None:
    """Elio feedback 2026-05-04: if every significant token appears only
    once, the emergent classifier shouldn't fabricate a theme. Better to
    return empty + uncertain so the user picks the theme."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt-BR",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    # Each significant token appears exactly once; no theme should be proposed.
    text = "carreira foco principal estrategia objetivo proposito"
    result = classify(text, config)
    assert result.primary == ""
    assert result.uncertain is True


def test_emergent_language_pt_alias_filters_ptbr_stopwords(tmp_path: Path) -> None:
    """v0.3.1: pt is an alias for pt-BR. A vault configured with `language: pt`
    must filter pt-BR pronouns and connectors (`eu`, `que`) the same as
    `language: pt-BR`. Pins the alias contract from classify.py:101."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    text = (
        "Eu quero que isso funcione e eu acho que carreira "
        "carreira eh o foco principal agora."
    )
    result = classify(text, config)
    assert result.primary == "carreira"
    assert result.primary not in {"eu", "que", "o", "e"}


def test_emergent_unknown_language_falls_back_to_english(tmp_path: Path) -> None:
    """v0.3.1: an unrecognized language code (e.g. `es`) must not crash; it
    falls back to English stopwords. Pins `_stopwords_for` default behavior
    so a typo'd or unsupported language config does not raise."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="es",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    # English stopwords filter "the" and "is"; "carreira" repeated qualifies as theme.
    text = "the carreira is the carreira focus today."
    # Should not raise even though es has no dedicated stopword set.
    result = classify(text, config)
    assert result.primary == "carreira"


def test_emergent_para_dirs_never_become_theme_candidates(tmp_path: Path) -> None:
    """Codex review of v0.3.0 (P0): the PARA-nested layout introduced in
    v0.3.0 creates `<vault>/Areas/` for fixed_domains configs. If a vault
    is in emergent mode and a PARA convention dir exists, it must not
    become a phantom theme candidate. With no matching content in input,
    pre-fix the alphabetically-first folder (Areas) was promoted as
    primary; post-fix, primary stays empty and route falls back to _inbox."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    (tmp_path / "Areas").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Resources").mkdir()
    (tmp_path / "Archives").mkdir()

    # Generic content with no folder-name overlap and no repeated content tokens
    result = classify("Some generic content here.", config)

    assert result.primary != "Areas"
    assert result.primary != "Projects"
    assert result.primary != "Resources"
    assert result.primary != "Archives"
    for forbidden in ("Areas", "Projects", "Resources", "Archives"):
        assert forbidden not in result.secondary


def test_emergent_areas_word_in_input_does_not_force_confident_routing(
    tmp_path: Path,
) -> None:
    """v0.3.1: even when input mentions 'areas' literally, the existence of
    a PARA `Areas/` folder must not push confidence high enough to bypass
    the uncertain gate. The PARA dir is filtered from theme candidates,
    so any 'areas' the user typed is handled by the propose-from-text path
    which sets uncertain=True."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    (tmp_path / "Areas").mkdir()

    result = classify("areas areas of focus matter today.", config)

    # `Areas` (capital A) must never be returned as a confident folder match.
    assert not (result.primary == "Areas" and result.uncertain is False)


def test_emergent_filters_ptbr_contractions(tmp_path: Path) -> None:
    """Codex review v0.3.0 follow-up: pt-BR contractions `pro`, `pros`, `pras`,
    `ai`, `aí` need to be in the stopword set. `pras` is exactly 4 chars and
    slips past _MIN_THEME_WORD_LEN; without filtering, repeated contractions
    surface as proposed themes."""
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt-BR",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    # pras and pros each appear twice (>= _MIN_THEME_FREQUENCY) and are 4 chars
    # (>= _MIN_THEME_WORD_LEN). Only stopword filtering keeps them out.
    text = "pras pras pros pros carreira"
    result = classify(text, config)
    assert result.primary not in {"pras", "pros", "pro", "ai", "aí"}
    for forbidden in ("pras", "pros", "pro", "ai", "aí"):
        assert forbidden not in result.secondary


def test_emergent_classify_filters_ptbr_stopwords(tmp_path: Path) -> None:
    """Elio feedback 2026-05-04: emergent mode classified pt-BR braindumps
    as 'que' and 'eu' because the English-only stopword filter let common
    Portuguese pronouns and connectors through. With pt-BR stopwords
    applied, the proposed theme should be a content word (carreira), not
    a pronoun (eu) or conjunction (que)."""
    config = _make_emergent_config(tmp_path)
    # Replace default emergent config to use pt-BR language
    config = Config(
        vault_path=tmp_path,
        mode="emergent",
        domains=(),
        notebook_map=MappingProxyType({}),
        language="pt-BR",
        skip_notebooklm=False,
        refinement_enabled=False,
        classification_confidence_threshold=0.6,
    )
    text = (
        "Eu quero que isso funcione e eu acho que carreira "
        "carreira eh o foco principal agora."
    )
    result = classify(text, config)
    assert result.primary == "carreira"
    forbidden = {"eu", "que", "o", "e", "a"}
    assert result.primary not in forbidden
    assert not (set(result.secondary) & forbidden)
