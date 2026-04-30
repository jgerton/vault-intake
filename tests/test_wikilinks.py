"""Tests for Step 6: generate wikilinks (fixed_domains track).

Covers the fixed_domains shape end-to-end. Emergent mode raises
NotImplementedError in v1; emergent track lands in a separate session.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.para import ParaResult
from vault_intake.wikilinks import (
    Wikilink,
    WikilinkResult,
    generate_wikilinks,
)


def _make_config(
    vault_path: Path,
    *,
    mode: str = "fixed_domains",
    domains: tuple[Domain, ...] = (
        Domain(slug="ops", description="Operations and processes."),
        Domain(slug="branding", description="Brand identity and design."),
        Domain(slug="dev", description="Software development and engineering."),
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


def _write_note(
    path: Path,
    *,
    title: str | None = None,
    domain: str | None = None,
    body: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    if title is not None or domain is not None:
        parts.append("---")
        if title is not None:
            parts.append(f'title: "{title}"')
        if domain is not None:
            parts.append(f"domain: {domain}")
        parts.append("---")
    parts.append(body)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def test_generate_wikilinks_returns_wikilink_result(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    result = generate_wikilinks(
        text="some body",
        classification=_make_classification(),
        para=_make_para(),
        config=config,
    )
    assert isinstance(result, WikilinkResult)
    assert result.mode == "fixed_domains"
    assert isinstance(result.proposals, tuple)


def test_cross_domain_link_proposed_when_existing_note_in_secondary_domain(
    tmp_path: Path,
) -> None:
    _write_note(
        tmp_path / "insights" / "brand-voice.md",
        title="Brand voice principles",
        domain="branding",
    )
    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Operations note that touches brand voice considerations.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "Brand voice principles" in targets
    cross = next(w for w in result.proposals if w.target == "Brand voice principles")
    assert cross.weight == 4
    assert cross.source_path == tmp_path / "insights" / "brand-voice.md"
    assert "cross-domain" in cross.reason.lower()


def test_active_project_link_proposed_when_para_category_is_project(
    tmp_path: Path,
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    para = _make_para(category="project", project_slug="launch-redesign")

    result = generate_wikilinks(
        text="Working on launch-redesign deck.",
        classification=_make_classification(primary="ops"),
        para=para,
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "launch-redesign" in targets
    project = next(w for w in result.proposals if w.target == "launch-redesign")
    assert project.weight == 3
    assert project.source_path == projects_dir / "launch-redesign.md"
    assert "project" in project.reason.lower()


def test_concept_overlap_requires_two_token_floor(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "single-token.md",
        title="Onboarding checklist",
        domain="ops",
    )
    _write_note(
        tmp_path / "insights" / "two-token.md",
        title="Quarterly review process",
        domain="ops",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=())
    text = (
        "Notes on the quarterly review process for the ops team. "
        "Touches on workflow audits and weekly cadence."
    )

    result = generate_wikilinks(
        text=text,
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "Quarterly review process" in targets
    assert "Onboarding checklist" not in targets
    overlap = next(w for w in result.proposals if w.target == "Quarterly review process")
    assert overlap.weight == 2
    assert "concept" in overlap.reason.lower()


def test_empty_backlog_marker_for_user_typed_wikilink_to_uncreated_note(
    tmp_path: Path,
) -> None:
    _write_note(
        tmp_path / "insights" / "existing.md",
        title="Existing note",
        domain="ops",
    )

    config = _make_config(tmp_path)
    body = (
        "Reflecting on this and linking back to [[Existing note]] but also "
        "to [[Future Concept]] which we have not captured yet."
    )

    result = generate_wikilinks(
        text=body,
        classification=_make_classification(primary="ops"),
        para=_make_para(),
        config=config,
    )

    targets = {w.target: w for w in result.proposals}
    assert "Future Concept" in targets
    marker = targets["Future Concept"]
    assert marker.weight == 1
    assert marker.source_path is None
    assert "uncreated" in marker.reason.lower() or "backlog" in marker.reason.lower()


def test_existing_typed_wikilink_does_not_emit_backlog_marker(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "existing.md",
        title="Existing note",
        domain="ops",
    )
    config = _make_config(tmp_path)
    body = "Linking back to [[Existing note]] which already exists."

    result = generate_wikilinks(
        text=body,
        classification=_make_classification(primary="ops"),
        para=_make_para(),
        config=config,
    )

    backlog = [w for w in result.proposals if w.weight == 1]
    assert backlog == []


def test_weighting_order_cross_domain_beats_project_beats_overlap_beats_marker(
    tmp_path: Path,
) -> None:
    _write_note(
        tmp_path / "insights" / "branding-launch.md",
        title="Branding launch playbook",
        domain="branding",
    )
    _write_note(
        tmp_path / "insights" / "ops-overlap.md",
        title="ops overlap notes",
        domain="ops",
    )
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "launch-redesign.md").write_text("# Launch\n", encoding="utf-8")

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))
    para = _make_para(category="project", project_slug="launch-redesign")
    body = (
        "Body about ops overlap notes and launch-redesign. "
        "Ties into [[Future Concept]] not yet captured."
    )

    result = generate_wikilinks(
        text=body,
        classification=classification,
        para=para,
        config=config,
    )

    weights = [w.weight for w in result.proposals]
    assert weights == sorted(weights, reverse=True)
    by_target = {w.target: w for w in result.proposals}
    assert by_target["Branding launch playbook"].weight == 4
    assert by_target["launch-redesign"].weight == 3
    assert by_target["ops overlap notes"].weight == 2
    assert by_target["Future Concept"].weight == 1


def test_max_proposals_caps_at_seven(tmp_path: Path) -> None:
    for i in range(10):
        _write_note(
            tmp_path / "insights" / f"note-{i}.md",
            title=f"Branding topic {i}",
            domain="branding",
        )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Some ops body content.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    assert len(result.proposals) <= 7
    assert result.candidates_considered >= 10


def test_returns_fewer_than_min_target_when_only_one_candidate(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "lonely.md",
        title="Lonely branding note",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Some ops body.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    assert len(result.proposals) == 1
    assert result.proposals[0].target == "Lonely branding note"


def test_empty_vault_returns_empty_proposals(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    result = generate_wikilinks(
        text="Some body.",
        classification=_make_classification(primary="ops"),
        para=_make_para(),
        config=config,
    )
    assert result.proposals == ()
    assert result.candidates_considered == 0


def test_dedupes_when_same_note_qualifies_for_multiple_categories(
    tmp_path: Path,
) -> None:
    _write_note(
        tmp_path / "insights" / "shared.md",
        title="Brand launch redesign",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))
    body = "Discussing brand launch redesign decisions for ops."

    result = generate_wikilinks(
        text=body,
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert targets.count("Brand launch redesign") == 1
    chosen = next(w for w in result.proposals if w.target == "Brand launch redesign")
    assert chosen.weight == 4


def test_skips_indexes_folder(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "_indexes" / "branding.md",
        title="Branding index",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Some ops body.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "Branding index" not in targets


def test_tiebreak_by_recency_then_alphabetical(tmp_path: Path) -> None:
    older = _write_note(
        tmp_path / "insights" / "older.md",
        title="Aaa branding note",
        domain="branding",
    )
    middle = _write_note(
        tmp_path / "insights" / "middle.md",
        title="Bbb branding note",
        domain="branding",
    )
    newer = _write_note(
        tmp_path / "insights" / "newer.md",
        title="Ccc branding note",
        domain="branding",
    )
    base = time.time()
    os.utime(older, (base - 100, base - 100))
    os.utime(middle, (base - 50, base - 50))
    os.utime(newer, (base, base))

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Some body.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert targets[:3] == ["Ccc branding note", "Bbb branding note", "Aaa branding note"]


def test_alphabetical_when_recency_ties(tmp_path: Path) -> None:
    a = _write_note(
        tmp_path / "insights" / "alpha.md",
        title="Alpha branding",
        domain="branding",
    )
    b = _write_note(
        tmp_path / "insights" / "beta.md",
        title="Beta branding",
        domain="branding",
    )
    same = time.time()
    os.utime(a, (same, same))
    os.utime(b, (same, same))

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="Some body.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert targets == ["Alpha branding", "Beta branding"]


def test_emergent_mode_raises_not_implemented(tmp_path: Path) -> None:
    config = _make_config(tmp_path, mode="emergent", domains=())
    with pytest.raises(NotImplementedError, match=r"emergent"):
        generate_wikilinks(
            text="any",
            classification=_make_classification(mode="fixed_domains"),
            para=_make_para(),
            config=config,
        )


def test_uncertain_classification_returns_result_without_crashing(
    tmp_path: Path,
) -> None:
    _write_note(
        tmp_path / "insights" / "branding-note.md",
        title="Branding things",
        domain="branding",
    )
    config = _make_config(tmp_path)
    classification = _make_classification(
        primary="ops",
        secondary=(),
        confidence=0.2,
        uncertain=True,
    )

    result = generate_wikilinks(
        text="Vague capture.",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    assert isinstance(result, WikilinkResult)
    assert isinstance(result.proposals, tuple)


def test_filename_stem_used_when_frontmatter_title_missing(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "no-title-note.md",
        title=None,
        domain="branding",
        body="Plain body without frontmatter title.",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="ops body",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "no-title-note" in targets


def test_notes_without_frontmatter_are_skipped_for_domain_match(
    tmp_path: Path,
) -> None:
    note = tmp_path / "insights" / "raw.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Just plain text, no frontmatter at all.", encoding="utf-8")

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="ops body",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    cross = [w for w in result.proposals if w.weight == 4]
    assert cross == []


def test_skips_dot_directories(tmp_path: Path) -> None:
    _write_note(
        tmp_path / ".git" / "weird.md",
        title="Should not appear",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="ops body",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    targets = [w.target for w in result.proposals]
    assert "Should not appear" not in targets


def test_min_proposals_target_does_not_pad(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "one.md",
        title="One branding",
        domain="branding",
    )
    _write_note(
        tmp_path / "insights" / "two.md",
        title="Two branding",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="ops body.",
        classification=classification,
        para=_make_para(),
        config=config,
        min_proposals_target=5,
    )

    assert len(result.proposals) == 2


def test_custom_max_proposals_respected(tmp_path: Path) -> None:
    for i in range(6):
        _write_note(
            tmp_path / "insights" / f"note-{i}.md",
            title=f"Branding {i}",
            domain="branding",
        )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="ops body.",
        classification=classification,
        para=_make_para(),
        config=config,
        max_proposals=3,
    )

    assert len(result.proposals) == 3


def test_wikilink_dataclass_is_frozen() -> None:
    w = Wikilink(target="x", weight=4, source_path=None, reason="test")
    with pytest.raises(Exception):
        w.weight = 1  # type: ignore[misc]


def test_candidates_considered_counts_unique_candidates(tmp_path: Path) -> None:
    _write_note(
        tmp_path / "insights" / "a.md",
        title="A branding",
        domain="branding",
    )
    _write_note(
        tmp_path / "insights" / "b.md",
        title="B branding",
        domain="branding",
    )

    config = _make_config(tmp_path)
    classification = _make_classification(primary="ops", secondary=("branding",))

    result = generate_wikilinks(
        text="some body",
        classification=classification,
        para=_make_para(),
        config=config,
    )

    assert result.candidates_considered == 2


def test_project_link_includes_directory_when_no_md_file(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "ycah-relaunch").mkdir(parents=True)

    config = _make_config(tmp_path)
    para = _make_para(category="project", project_slug="ycah-relaunch")

    result = generate_wikilinks(
        text="Work on ycah-relaunch.",
        classification=_make_classification(primary="ops"),
        para=para,
        config=config,
    )

    project = next(w for w in result.proposals if w.target == "ycah-relaunch")
    assert project.source_path == projects_dir / "ycah-relaunch"


def test_project_link_source_path_none_when_project_dir_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    para = _make_para(category="project", project_slug="ghost-project")

    result = generate_wikilinks(
        text="Work on ghost-project.",
        classification=_make_classification(primary="ops"),
        para=para,
        config=config,
    )

    project = next(w for w in result.proposals if w.target == "ghost-project")
    assert project.weight == 3
    assert project.source_path is None
