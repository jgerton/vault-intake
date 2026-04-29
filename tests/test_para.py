"""Tests for Step 4: categorize PARA category (skipped in emergent mode).

Covers fixed_domains/para mode end-to-end. Emergent mode raises
NotImplementedError in v1; emergent track lands in a separate session.
"""
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.detect import DetectionResult
from vault_intake.para import ParaResult, categorize_para


def _make_config(
    vault_path: Path,
    *,
    mode: str = "fixed_domains",
    domains: tuple[Domain, ...] = (
        Domain(slug="ops", description="Operations and processes."),
        Domain(slug="branding", description="Brand identity and design."),
    ),
) -> Config:
    return Config(
        vault_path=vault_path,
        mode=mode,  # type: ignore[arg-type]
        domains=domains,
        notebook_map=MappingProxyType({}),
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


def test_project_attached_when_input_mentions_project_file_slug(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch Redesign\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Working on the launch-redesign deck this morning."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert isinstance(result, ParaResult)
    assert result.category == "project"
    assert result.project_slug == "launch-redesign"
    assert result.uncertain is False
    assert "project_slug_match" in result.signals


def test_project_attached_when_input_mentions_project_folder_slug(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "ycah-relaunch").mkdir(parents=True)

    config = _make_config(tmp_path)
    text = "Adding context to ycah-relaunch about the next milestone."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category == "project"
    assert result.project_slug == "ycah-relaunch"


def test_area_attached_when_no_project_mention_and_domain_in_scope(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "other-project.md").write_text("# Other\n", encoding="utf-8")

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", confidence=0.8, uncertain=False)
    text = "Tightening up our weekly review process across the team."

    result = categorize_para(text, _make_detection(), classification, config)

    assert result.category == "area"
    assert result.project_slug is None
    assert result.uncertain is False
    assert "domain_in_scope" in result.signals


def test_resource_when_detection_is_reference(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()

    config = _make_config(tmp_path)
    detection = _make_detection(type="reference", signals=("url_present",))
    text = "Check this article: https://example.com/great-essay"

    result = categorize_para(text, detection, _make_classification(), config)

    assert result.category == "resource"
    assert result.project_slug is None
    assert "reference_content_type" in result.signals


def test_archive_when_text_contains_deprecation_phrasing(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()

    config = _make_config(tmp_path)
    text = "We used to handle onboarding through email but the old approach was deprecated."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category == "archive"
    assert "archive_phrasing" in result.signals


def test_project_takes_priority_over_archive_with_uncertain_flag(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "On launch-redesign: the old approach was deprecated but we kept the assets."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category == "project"
    assert result.project_slug == "launch-redesign"
    assert result.uncertain is True
    assert "project_slug_match" in result.signals
    assert "archive_phrasing" in result.signals


def test_falls_back_to_area_when_projects_dir_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", confidence=0.8, uncertain=False)
    text = "Process improvement notes for the operations workflow."

    result = categorize_para(text, _make_detection(), classification, config)

    assert result.category == "area"
    assert result.project_slug is None


def test_uncertain_when_classification_uncertain_and_no_other_signals(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", confidence=0.2, uncertain=True)
    text = "Some scattered thoughts I need to capture."

    result = categorize_para(text, _make_detection(), classification, config)

    assert result.category == "area"
    assert result.uncertain is True


def test_emergent_mode_raises_not_implemented(tmp_path: Path) -> None:
    config = _make_config(tmp_path, mode="emergent", domains=())

    with pytest.raises(NotImplementedError, match=r"emergent"):
        categorize_para(
            "anything",
            _make_detection(),
            _make_classification(mode="fixed_domains"),
            config,
        )


def test_only_matches_project_slugs_in_projects_dir(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")
    other_dir = tmp_path / "references"
    other_dir.mkdir()
    (other_dir / "external-asset.md").write_text("# Asset\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Reading external-asset notes today, no launch reference here."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category != "project"
    assert result.project_slug is None


def test_ignores_hidden_files_in_projects_dir(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / ".hidden.md").write_text("# Hidden\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Looking at .hidden today."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category != "project"


def test_project_slug_match_is_case_insensitive(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Reviewing the LAUNCH-REDESIGN copy deck."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category == "project"
    assert result.project_slug == "launch-redesign"


def test_multiple_matching_project_slugs_pick_alphabetically_first(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "zebra-rollout.md").write_text("# Zebra\n", encoding="utf-8")
    (projects_dir / "alpha-launch.md").write_text("# Alpha\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Cross-cutting note that mentions both alpha-launch and zebra-rollout."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category == "project"
    assert result.project_slug == "alpha-launch"


def test_slug_must_match_at_word_boundary_not_substring(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    text = "Pre-launching the relaunched landing page; launchpad ready."

    result = categorize_para(text, _make_detection(), _make_classification(), config)

    assert result.category != "project"
    assert result.project_slug is None


def test_signals_records_all_fired_signals_for_audit(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    detection = _make_detection(type="reference", signals=("url_present",))
    text = "On launch-redesign: see https://example.com for the old approach we deprecated."

    result = categorize_para(text, detection, _make_classification(), config)

    assert result.category == "project"
    assert "project_slug_match" in result.signals
    assert "reference_content_type" in result.signals
    assert "archive_phrasing" in result.signals
