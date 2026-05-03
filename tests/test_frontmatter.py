"""Tests for Step 5: generate frontmatter (mode-dependent).

Covers fixed_domains mode end-to-end. Emergent mode raises
NotImplementedError in v1; emergent track lands in a separate session.
"""
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.detect import DetectionResult
from vault_intake.frontmatter import Frontmatter, generate_frontmatter
from vault_intake.para import ParaResult
from vault_intake.refine import RefinedContent


def _make_config(
    vault_path: Path,
    *,
    mode: str = "fixed_domains",
    domains: tuple[Domain, ...] = (
        Domain(slug="ops", description="Operations and processes."),
        Domain(slug="branding", description="Brand identity and design."),
    ),
    notebook_map: dict[str, str] | None = None,
) -> Config:
    return Config(
        vault_path=vault_path,
        mode=mode,  # type: ignore[arg-type]
        domains=domains,
        notebook_map=MappingProxyType(dict(notebook_map or {})),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=True,
        classification_confidence_threshold=0.6,
    )


def _make_detection(
    *,
    type: str = "note",
    uncertain: bool = False,
    signals: tuple[str, ...] = (),
    refinement_applicable: bool = False,
) -> DetectionResult:
    return DetectionResult(
        type=type,  # type: ignore[arg-type]
        uncertain=uncertain,
        signals=signals,
        refinement_applicable=refinement_applicable,
    )


def _make_classification(
    *,
    primary: str = "ops",
    secondary: tuple[str, ...] = (),
    confidence: float = 0.8,
    uncertain: bool = False,
    mode: str = "fixed_domains",
) -> ClassificationResult:
    return ClassificationResult(
        primary=primary,
        secondary=secondary,
        confidence=confidence,
        uncertain=uncertain,
        mode=mode,  # type: ignore[arg-type]
    )


def _make_para(
    *,
    category: str = "area",
    project_slug: str | None = None,
    uncertain: bool = False,
    signals: tuple[str, ...] = (),
) -> ParaResult:
    return ParaResult(
        category=category,  # type: ignore[arg-type]
        project_slug=project_slug,
        uncertain=uncertain,
        signals=signals,
    )


def test_full_happy_path_populates_all_fields(tmp_path: Path) -> None:
    config = _make_config(tmp_path, notebook_map={"ops": "nb-ops-id"})
    text = "# Weekly review notes\n\nProcess improvements for the team."
    detection = _make_detection(type="note")
    refinement = RefinedContent(
        refined="Process improvements for the team.",
        original=text,
        changed=True,
    )
    classification = _make_classification(
        primary="ops", secondary=("branding",), confidence=0.85
    )
    para = _make_para(category="area", signals=("domain_in_scope",))

    fm = generate_frontmatter(
        text=text,
        detection=detection,
        refinement=refinement,
        classification=classification,
        para=para,
        config=config,
        source_type="paste",
        source_uri="",
        captured_at="2026-04-29",
    )

    assert isinstance(fm, Frontmatter)
    # OS-wide baseline (architecture plan Section 1.4.1)
    assert fm.schema_version == "1.0"
    assert fm.source_type == "paste"
    assert fm.source_uri == ""
    assert fm.captured_at == "2026-04-29"
    assert fm.processed_by == "/vault-intake"
    assert fm.confidence == 0.85
    assert fm.original_ref == "## Captura original"
    # Cross-track conventions
    assert fm.title == "weekly-review-notes"
    assert fm.date == "2026-04-29"
    # Fixed_domains track additions
    assert fm.type == "note"
    assert fm.domain == "ops"
    assert fm.tags == ("ops", "branding")
    assert fm.notebook == "nb-ops-id"
    assert fm.source_id == ""
    assert fm.project == ""


def test_title_uses_markdown_h1_when_present(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    text = "# My Important Note\n\nBody content goes here."

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "my-important-note"


def test_title_falls_back_to_first_sentence(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    text = "Quick note about the new pricing model. More details follow."

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "quick-note-about-the-new-pricing-model"


def test_title_fallback_when_input_is_empty(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    fm = generate_frontmatter(
        text="   \n\n   ",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "note-2026-04-29"


def test_title_capped_at_60_chars(tmp_path: Path) -> None:
    """Title cap is 60 chars (lowered from 80 on 2026-04-30 to fix ugly filenames)."""
    config = _make_config(tmp_path)
    text = "# " + ("word " * 30)

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert len(fm.title) <= 60
    assert not fm.title.endswith("-")


def test_title_cuts_at_word_boundary_not_mid_word(tmp_path: Path) -> None:
    """A long word straddling the cap must be dropped, not truncated mid-word.

    Pre-fix behavior took slugged[:cap].rstrip('-'), which left mid-word
    truncations like 'antidisestabli' in the title when a long word
    happened to span the cap boundary. Post-fix cuts back to the last
    hyphen at or before the cap so titles always end on a clean word.
    """
    config = _make_config(tmp_path)
    text = "# one two three four five six seven eight nine antidisestablishmentarianism"

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert "antidisestabli" not in fm.title, (
        f"mid-word truncation in title: {fm.title!r}"
    )
    assert fm.title == "one-two-three-four-five-six-seven-eight-nine"


def test_title_prefers_short_complete_sentence_over_long_truncated_one(
    tmp_path: Path,
) -> None:
    """When no H1 and first sentence overflows the cap, prefer the next short one.

    Real-world driver: 2026-04-30 first-vault capture produced
    'today-shipped-the-skill-install-sync-mechanism-for-vault-intake-the-final-piece'
    because the first sentence ran past the 80-char cap and got
    truncated. Walking sentences in order and preferring the first one
    that fits the cap yields cleaner titles.
    """
    config = _make_config(tmp_path)
    text = (
        "This is a very long opening sentence that exceeds the sixty "
        "character cap easily and would otherwise be truncated. Brief next."
    )

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "brief-next"


def test_title_falls_back_to_date_when_single_word_overflows_cap(
    tmp_path: Path,
) -> None:
    """A single token longer than the cap has no word boundary; date fallback wins.

    The word-boundary contract forbids mid-word truncation. When no hyphen
    exists in the first cap chars (one giant unhyphenated token), _slugify
    returns empty so _build_title yields 'note-{date}' instead of cutting
    mid-character.
    """
    config = _make_config(tmp_path)
    text = "# " + "a" * 100

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "note-2026-04-29"


def test_title_strips_punctuation_and_normalizes_accents(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    text = "# Reunião: planejamento, estratégia e execução!"

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.title == "reuniao-planejamento-estrategia-e-execucao"


def test_tags_seed_from_primary_and_secondary(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(
        primary="ops",
        secondary=("branding", "research"),
        confidence=0.8,
    )

    fm = generate_frontmatter(
        text="Notes about ops and branding.",
        detection=_make_detection(),
        refinement=None,
        classification=classification,
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.tags == ("ops", "branding", "research")


def test_tags_capped_at_5(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(
        primary="ops",
        secondary=("a", "b", "c", "d", "e", "f", "g"),
        confidence=0.8,
    )

    fm = generate_frontmatter(
        text="Body text.",
        detection=_make_detection(),
        refinement=None,
        classification=classification,
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert len(fm.tags) == 5
    assert fm.tags == ("ops", "a", "b", "c", "d")


def test_tags_empty_when_classification_uncertain(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(
        primary="ops", secondary=("branding",), confidence=0.2, uncertain=True
    )

    fm = generate_frontmatter(
        text="Vague body.",
        detection=_make_detection(),
        refinement=None,
        classification=classification,
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.tags == ()


def test_project_field_set_when_para_is_project(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    para = _make_para(
        category="project",
        project_slug="launch-redesign",
        signals=("project_slug_match",),
    )

    fm = generate_frontmatter(
        text="Working on launch-redesign.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=para,
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.project == "launch-redesign"


def test_project_field_empty_when_para_not_project(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    para = _make_para(category="area")

    fm = generate_frontmatter(
        text="General process notes.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=para,
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.project == ""


def test_refinement_none_means_original_ref_empty(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    fm = generate_frontmatter(
        text="Already-structured document body.",
        detection=_make_detection(type="document"),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.original_ref == ""


def test_refinement_unchanged_means_original_ref_empty(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    text = "Already-clean prose."
    refinement = RefinedContent(refined=text, original=text, changed=False)

    fm = generate_frontmatter(
        text=text,
        detection=_make_detection(),
        refinement=refinement,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.original_ref == ""


def test_refinement_changed_sets_original_ref_marker(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    refinement = RefinedContent(
        refined="Cleaner prose.",
        original="Cleaner, tipo, prose.",
        changed=True,
    )

    fm = generate_frontmatter(
        text="Cleaner, tipo, prose.",
        detection=_make_detection(),
        refinement=refinement,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.original_ref == "## Captura original"


def test_notebook_lookup_hit(tmp_path: Path) -> None:
    config = _make_config(tmp_path, notebook_map={"ops": "nb-ops-id"})

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.notebook == "nb-ops-id"


def test_notebook_lookup_miss_returns_empty(tmp_path: Path) -> None:
    config = _make_config(tmp_path, notebook_map={"branding": "nb-branding-id"})

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.notebook == ""


def test_confidence_preserved_as_float(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(confidence=0.73)

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=classification,
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert isinstance(fm.confidence, float)
    assert fm.confidence == 0.73


def test_emergent_mode_raises_not_implemented(tmp_path: Path) -> None:
    config = _make_config(tmp_path, mode="emergent", domains=())

    with pytest.raises(NotImplementedError, match=r"emergent"):
        generate_frontmatter(
            text="Body.",
            detection=_make_detection(),
            refinement=None,
            classification=_make_classification(mode="emergent"),
            para=_make_para(),
            config=config,
            captured_at="2026-04-29",
        )


def test_captured_at_defaults_to_today_iso(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
    )

    today = date.today().isoformat()
    assert fm.captured_at == today
    assert fm.date == today


def test_source_metadata_defaults(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.source_type == "paste"
    assert fm.source_uri == ""
    assert fm.source_id == ""


def test_to_yaml_round_trips_with_pyyaml(tmp_path: Path) -> None:
    config = _make_config(tmp_path, notebook_map={"ops": "nb-ops-id"})
    refinement = RefinedContent(
        refined="Refined body.",
        original="Original body, tipo.",
        changed=True,
    )
    para = _make_para(
        category="project",
        project_slug="launch-redesign",
        signals=("project_slug_match",),
    )

    fm = generate_frontmatter(
        text="# Launch redesign retro\n\nNotes from the post-mortem.",
        detection=_make_detection(type="session"),
        refinement=refinement,
        classification=_make_classification(
            primary="ops", secondary=("branding",), confidence=0.85
        ),
        para=para,
        config=config,
        source_type="paste",
        source_uri="",
        captured_at="2026-04-29",
    )

    yaml_text = fm.to_yaml()
    loaded = yaml.safe_load(yaml_text)

    assert loaded["schema_version"] == "1.0"
    assert loaded["source_type"] == "paste"
    assert loaded["source_uri"] == ""
    assert loaded["captured_at"] == "2026-04-29"
    assert loaded["processed_by"] == "/vault-intake"
    assert loaded["confidence"] == 0.85
    assert loaded["original_ref"] == "## Captura original"
    assert loaded["title"] == "launch-redesign-retro"
    assert loaded["date"] == "2026-04-29"
    assert loaded["type"] == "project"  # PARA project overrides detection's "session"
    assert loaded["domain"] == "ops"
    assert loaded["tags"] == ["ops", "branding"]
    assert loaded["notebook"] == "nb-ops-id"
    assert loaded["source_id"] == ""
    assert loaded["project"] == "launch-redesign"


def test_to_yaml_emits_empty_string_for_none_confidence(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(confidence=0.0)

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(),
        refinement=None,
        classification=classification,
        para=_make_para(),
        config=config,
        captured_at="2026-04-29",
    )
    fm = Frontmatter(
        schema_version=fm.schema_version,
        source_type=fm.source_type,
        source_uri=fm.source_uri,
        captured_at=fm.captured_at,
        processed_by=fm.processed_by,
        confidence=None,
        original_ref=fm.original_ref,
        title=fm.title,
        date=fm.date,
        type=fm.type,
        domain=fm.domain,
        tags=fm.tags,
        notebook=fm.notebook,
        source_id=fm.source_id,
        project=fm.project,
    )

    yaml_text = fm.to_yaml()
    loaded = yaml.safe_load(yaml_text)
    assert loaded["confidence"] == ""


@pytest.mark.parametrize(
    "detection_type,expected_frontmatter_type",
    [
        ("session", "session"),
        ("document", "note"),
        ("reference", "reference"),
        ("context", "context"),
        ("prompt", "prompt"),
        ("transcription", "note"),
        ("note", "note"),
    ],
)
def test_detection_type_translates_to_frontmatter_type(
    tmp_path: Path,
    detection_type: str,
    expected_frontmatter_type: str,
) -> None:
    config = _make_config(tmp_path)

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(type=detection_type),
        refinement=None,
        classification=_make_classification(),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.type == expected_frontmatter_type


@pytest.mark.parametrize(
    "detection_type",
    ["session", "document", "reference", "context", "prompt", "transcription", "note"],
)
def test_para_project_overrides_detection_type(
    tmp_path: Path,
    detection_type: str,
) -> None:
    config = _make_config(tmp_path)
    para = _make_para(
        category="project",
        project_slug="launch-redesign",
        signals=("project_slug_match",),
    )

    fm = generate_frontmatter(
        text="Body.",
        detection=_make_detection(type=detection_type),
        refinement=None,
        classification=_make_classification(),
        para=para,
        config=config,
        captured_at="2026-04-29",
    )

    assert fm.type == "project"
    assert fm.project == "launch-redesign"


def test_insight_and_workflow_frontmatter_types_are_valid(tmp_path: Path) -> None:
    # `insight` and `workflow` are not auto-derived in v1: the rule-based
    # builder produces only `note`, `session`, `reference`, `context`,
    # `prompt`, and `project`. The skill orchestrator surfaces `insight`
    # and `workflow` to the user at confirmation time. The Frontmatter
    # dataclass must still accept both as valid Literal values so user-
    # set overrides round-trip cleanly.
    fm = Frontmatter(
        schema_version="1.0",
        source_type="paste",
        source_uri="",
        captured_at="2026-04-29",
        processed_by="/vault-intake",
        confidence=0.8,
        original_ref="",
        title="lesson-learned",
        date="2026-04-29",
        type="insight",
        domain="ops",
        tags=("ops",),
        notebook="",
        source_id="",
        project="",
    )
    yaml_text = fm.to_yaml()
    assert yaml.safe_load(yaml_text)["type"] == "insight"

    fm_wf = Frontmatter(
        schema_version="1.0",
        source_type="paste",
        source_uri="",
        captured_at="2026-04-29",
        processed_by="/vault-intake",
        confidence=0.8,
        original_ref="",
        title="release-checklist",
        date="2026-04-29",
        type="workflow",
        domain="ops",
        tags=("ops",),
        notebook="",
        source_id="",
        project="",
    )
    assert yaml.safe_load(fm_wf.to_yaml())["type"] == "workflow"


def test_to_yaml_field_order_is_canonical(tmp_path: Path) -> None:
    config = _make_config(tmp_path, notebook_map={"ops": "nb-ops-id"})

    fm = generate_frontmatter(
        text="# Order check\n\nBody.",
        detection=_make_detection(type="note"),
        refinement=None,
        classification=_make_classification(
            primary="ops", secondary=("branding",), confidence=0.8
        ),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-04-29",
    )

    yaml_text = fm.to_yaml()
    expected_order = [
        "schema_version",
        "source_type",
        "source_uri",
        "captured_at",
        "processed_by",
        "confidence",
        "original_ref",
        "title",
        "date",
        "type",
        "domain",
        "tags",
        "notebook",
        "source_id",
        "project",
    ]
    # Match each key at start of line so the bare "type:" search does
    # not falsely match inside "source_type:".
    actual_keys = [
        line.split(":", 1)[0]
        for line in yaml_text.splitlines()
        if line and not line.startswith((" ", "-"))
    ]
    assert actual_keys == expected_order, (
        f"YAML field order drifted from canonical baseline.\n"
        f"  expected: {expected_order}\n"
        f"  actual:   {actual_keys}\n"
        f"  yaml:     {yaml_text!r}"
    )


# ---------------------------------------------------------------------------
# Fix 2: pt-BR stopword filtering + braindump naming (M1.1)
# ---------------------------------------------------------------------------


def _make_ptbr_config(tmp_path: Path) -> "Config":
    """Config with language=pt-BR for stopword tests."""
    import dataclasses
    return dataclasses.replace(_make_config(tmp_path), language="pt-BR")


def test_ptbr_filler_word_as_h1_is_skipped(tmp_path: Path) -> None:
    """H1 that is a bare pt-BR filler word falls back to first content sentence."""
    config = _make_ptbr_config(tmp_path)
    fm = generate_frontmatter(
        text="# Certo\n\nMinha marca pessoal no mercado digital.",
        detection=_make_detection(type="note"),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert fm.title != "certo"
    assert "marca" in fm.title or "pessoal" in fm.title or "digital" in fm.title


def test_ptbr_filler_at_sentence_start_is_skipped(tmp_path: Path) -> None:
    """First sentence starting with a pt-BR filler word is skipped; next used."""
    config = _make_ptbr_config(tmp_path)
    fm = generate_frontmatter(
        text="Certo, entao vou falar. Criando uma estrategia de marca.",
        detection=_make_detection(type="note"),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert not fm.title.startswith("certo")
    assert "criando" in fm.title or "estrategia" in fm.title or "marca" in fm.title


def test_en_stopwords_not_applied_to_ptbr_config(tmp_path: Path) -> None:
    """Common English content words are not treated as stopwords in pt-BR mode."""
    config = _make_ptbr_config(tmp_path)
    fm = generate_frontmatter(
        text="# Ok so here is the plan for branding.",
        detection=_make_detection(type="note"),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert fm.title != "note-2026-05-02"


def test_braindump_title_uses_braindump_prefix(tmp_path: Path) -> None:
    """Notes with refinement_applicable=True use braindump-<slug>-date naming."""
    config = _make_config(tmp_path)
    fm = generate_frontmatter(
        text="Quick brain dump about the deployment pipeline and rollout plan.",
        detection=_make_detection(type="note", refinement_applicable=True),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert fm.title.startswith("braindump-")


def test_braindump_title_includes_date_suffix(tmp_path: Path) -> None:
    """Braindump title ends with the capture date."""
    config = _make_config(tmp_path)
    fm = generate_frontmatter(
        text="Some unstructured thoughts about the ops rollout.",
        detection=_make_detection(type="note", refinement_applicable=True),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert fm.title.endswith("-2026-05-02")


def test_braindump_with_ptbr_filler_skips_filler(tmp_path: Path) -> None:
    """Braindump in pt-BR vault skips filler words in the slug."""
    config = _make_ptbr_config(tmp_path)
    fm = generate_frontmatter(
        text="Certo entao. Estrategia de posicionamento de marca.",
        detection=_make_detection(type="note", refinement_applicable=True),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert fm.title.startswith("braindump-")
    assert not fm.title.startswith("braindump-certo")


def test_non_braindump_note_unchanged(tmp_path: Path) -> None:
    """Regular note (refinement_applicable=False) is not prefixed with braindump."""
    config = _make_config(tmp_path)
    fm = generate_frontmatter(
        text="# My structured note\n\nWith clear heading.",
        detection=_make_detection(type="note", refinement_applicable=False),
        refinement=None,
        classification=_make_classification(primary="ops"),
        para=_make_para(category="area"),
        config=config,
        captured_at="2026-05-02",
    )
    assert not fm.title.startswith("braindump-")
