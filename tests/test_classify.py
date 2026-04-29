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


def test_emergent_mode_raises_not_implemented():
    config = _make_config(mode="emergent", domains=())

    with pytest.raises(NotImplementedError, match=r"emergent"):
        classify("any content", config)


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
