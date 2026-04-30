"""Tests for Step 8: route to destination folder (mode-dependent).

Per build spec lines 184-214: routing returns a path-suggestion plus
audit metadata. The orchestrator handles file writes at session-end
confirmation; `route()` itself is pure.

fixed_domains/para mode follows the (type, PARA) destination table.
emergent mode looks up `classification.primary` (theme) against
existing vault folders, falling back to `_inbox/` when no folder
matches.

Function-side gate is unconditional; the orchestrator decides whether
to invoke based on context. Both modes ship in this session.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.detect import ContentType, DetectionResult
from vault_intake.frontmatter import Frontmatter, NoteType
from vault_intake.para import ParaCategory, ParaResult
from vault_intake.route import RouteResult, route


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_config(
    *,
    mode: str = "fixed_domains",
    vault_path: Path | None = None,
) -> Config:
    return Config(
        vault_path=vault_path or Path("/tmp/vault-stub"),
        mode=mode,  # type: ignore[arg-type]
        domains=(
            Domain(slug="ops", description="Operations and processes."),
            Domain(slug="branding", description="Brand identity and design."),
            Domain(slug="dev", description="Software development and engineering."),
        ) if mode == "fixed_domains" else (),
        notebook_map=MappingProxyType({}),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=True,
        classification_confidence_threshold=0.6,
    )


def _make_detection(content_type: ContentType = "session") -> DetectionResult:
    return DetectionResult(
        type=content_type,
        uncertain=False,
        signals=(),
        refinement_applicable=False,
    )


def _make_classification(primary: str = "ops") -> ClassificationResult:
    return ClassificationResult(
        primary=primary,
        secondary=(),
        confidence=0.8,
        uncertain=False,
        mode="fixed_domains",
    )


def _make_para(
    *,
    category: ParaCategory = "area",
    project_slug: str | None = None,
) -> ParaResult:
    return ParaResult(
        category=category,
        project_slug=project_slug,
        uncertain=False,
        signals=(),
    )


def _make_frontmatter(
    *,
    note_type: NoteType = "session",
    title: str = "test-note",
    domain: str = "ops",
    project: str = "",
) -> Frontmatter:
    return Frontmatter(
        schema_version="1.0",
        source_type="paste",
        source_uri="",
        captured_at="2026-04-30",
        processed_by="/vault-intake",
        confidence=0.8,
        original_ref="",
        title=title,
        date="2026-04-30",
        type=note_type,
        domain=domain,
        tags=(),
        notebook="",
        source_id="",
        project=project,
    )


# ---------------------------------------------------------------------------
# Round 1: RouteResult shape and frozenness
# ---------------------------------------------------------------------------


def test_route_result_is_frozen_dataclass():
    result = RouteResult(
        destination=Path("/vault/sessions"),
        project_link_target=None,
        archive_flagged=False,
        inbox_fallback=False,
        is_section_update=False,
        reason="test",
        mode="fixed_domains",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.destination = Path("/elsewhere")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round 2: fixed_domains destination table (10 rows from spec lines 190-201)
# ---------------------------------------------------------------------------


def test_session_project_routes_to_sessions_with_link_target(tmp_path):
    # PARA-project override (Step 5 _derive_note_type) sets
    # frontmatter.type=project whenever para.category=project. Routing
    # uses detection.type to recover the original session-vs-context
    # distinction.
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="project", project="launch-redesign"),
        config=config,
    )
    assert result.destination == tmp_path / "sessions"
    assert result.project_link_target == tmp_path / "projects" / "launch-redesign.md"
    assert result.is_section_update is False
    assert result.archive_flagged is False
    assert result.inbox_fallback is False


def test_session_area_routes_to_sessions_no_link(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.destination == tmp_path / "sessions"
    assert result.project_link_target is None


def test_insight_any_routes_to_insights(tmp_path):
    config = _make_config(vault_path=tmp_path)
    for category in ("project", "area", "resource"):
        result = route(
            detection=_make_detection("note"),
            classification=_make_classification(),
            para=_make_para(category=category, project_slug="x" if category == "project" else None),  # type: ignore[arg-type]
            frontmatter=_make_frontmatter(note_type="insight"),
            config=config,
        )
        assert result.destination == tmp_path / "insights", f"category={category}"


def test_workflow_any_routes_to_workflows(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="workflow"),
        config=config,
    )
    assert result.destination == tmp_path / "workflows"


def test_prompt_any_routes_to_prompts(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("prompt"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="prompt"),
        config=config,
    )
    assert result.destination == tmp_path / "prompts"


def test_context_area_routes_to_context(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("context"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="context"),
        config=config,
    )
    assert result.destination == tmp_path / "context"
    assert result.is_section_update is False


def test_context_project_routes_to_project_file_section_update(tmp_path):
    # PARA-project override sets frontmatter.type=project; detection.type
    # remains "context" so the routing key recovers spec line 198's
    # context+project case (section update on projects/{slug}.md).
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("context"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="project", project="launch-redesign"),
        config=config,
    )
    assert result.destination == tmp_path / "projects" / "launch-redesign.md"
    assert result.is_section_update is True
    assert result.project_link_target == tmp_path / "projects" / "launch-redesign.md"


def test_reference_resource_routes_to_references(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("reference"),
        classification=_make_classification(),
        para=_make_para(category="resource"),
        frontmatter=_make_frontmatter(note_type="reference"),
        config=config,
    )
    assert result.destination == tmp_path / "references"


def test_note_area_routes_to_sessions_no_link(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="note"),
        config=config,
    )
    assert result.destination == tmp_path / "sessions"
    assert result.project_link_target is None


def test_note_project_routes_to_sessions_with_link_target(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="project", project="launch-redesign"),
        config=config,
    )
    # Note: PARA-project override sets frontmatter.type=project; routing
    # for note+project should land in sessions with a link, per spec
    # line 201 ("note + Project-attached: sessions/ + link in project file").
    # Frontmatter.type=project is the routing-key for context+project
    # (section update), not for note+project. Distinguish via the
    # original detection.type (note) vs the frontmatter.type (project).
    assert result.destination == tmp_path / "sessions"
    assert result.project_link_target == tmp_path / "projects" / "launch-redesign.md"
    assert result.is_section_update is False


# ---------------------------------------------------------------------------
# Round 3: archive flagging (spec line 203 - flagged, not auto-routed)
# ---------------------------------------------------------------------------


def test_archive_para_flags_without_auto_routing(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="archive"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.archive_flagged is True
    # Destination is the would-be target if archive were ignored, so the
    # orchestrator can offer "route here, or move to archive/".
    assert result.destination == tmp_path / "sessions"


def test_archive_flagged_for_insight(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=_make_classification(),
        para=_make_para(category="archive"),
        frontmatter=_make_frontmatter(note_type="insight"),
        config=config,
    )
    assert result.archive_flagged is True
    assert result.destination == tmp_path / "insights"


# ---------------------------------------------------------------------------
# Round 4: unlisted (type, PARA) fallback to _inbox/
# ---------------------------------------------------------------------------


def test_unlisted_combo_falls_back_to_inbox(tmp_path):
    config = _make_config(vault_path=tmp_path)
    # `reference` type with `area` PARA is not in the spec's table.
    result = route(
        detection=_make_detection("reference"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="reference"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True


def test_unlisted_session_resource_falls_back_to_inbox(tmp_path):
    # session + resource is not in the spec's table.
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="resource"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True


# ---------------------------------------------------------------------------
# Round 4b: PARA-project override interacts correctly with user-set types
# (Codex R-1: prompt+project must stay in prompts/, not sessions/+link;
# reference+project is unlisted and must fall back to _inbox/.)
# ---------------------------------------------------------------------------


def test_prompt_with_para_project_override_routes_to_prompts(tmp_path):
    # Step 5's PARA-project override sets frontmatter.type=project
    # unconditionally. Step 8 must recover the original detection.type
    # ("prompt") and route to prompts/, NOT to sessions/+link, since
    # spec line 196 says "prompt | any | prompts/".
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("prompt"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="project", project="launch-redesign"),
        config=config,
    )
    assert result.destination == tmp_path / "prompts"
    assert result.project_link_target is None
    assert result.is_section_update is False
    assert result.archive_flagged is False
    assert result.inbox_fallback is False


def test_reference_with_para_project_override_falls_back_to_inbox(tmp_path):
    # reference + project is not in the spec's table. After Step 5's
    # PARA-project override, Step 8 sees frontmatter.type=project,
    # detection.type=reference. It must fall back to _inbox/, not
    # mis-route to sessions/+link.
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("reference"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="project", project="launch-redesign"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True
    assert result.project_link_target is None


def test_user_set_insight_with_para_project_routes_to_insights(tmp_path):
    # Orchestrator may override frontmatter.type to "insight" at user
    # confirmation, even when para.category="project". The user's
    # explicit type wins; insights always route to insights/ per spec
    # line 194.
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=_make_classification(),
        para=_make_para(category="project", project_slug="launch-redesign"),
        frontmatter=_make_frontmatter(note_type="insight"),
        config=config,
    )
    assert result.destination == tmp_path / "insights"
    assert result.project_link_target is None


# ---------------------------------------------------------------------------
# Round 5: emergent mode - exact theme folder match
# ---------------------------------------------------------------------------


def test_emergent_routes_to_existing_theme_folder_exact_match(tmp_path):
    (tmp_path / "consciencia").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="consciencia",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note", domain="consciencia"),
        config=config,
    )
    assert result.destination == tmp_path / "consciencia"
    assert result.inbox_fallback is False
    assert result.mode == "emergent"


def test_emergent_routes_to_existing_theme_folder_slug_variant(tmp_path):
    (tmp_path / "branding-system").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="Branding System",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note", domain="Branding System"),
        config=config,
    )
    assert result.destination == tmp_path / "branding-system"
    assert result.inbox_fallback is False


# ---------------------------------------------------------------------------
# Round 6: emergent mode - inbox fallback when no folder matches
# ---------------------------------------------------------------------------


def test_emergent_falls_back_to_inbox_when_no_folder_matches(tmp_path):
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="brand-new-theme",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note", domain="brand-new-theme"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True
    assert result.mode == "emergent"


def test_emergent_inbox_fallback_when_vault_has_no_folders(tmp_path):
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="anything",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note", domain="anything"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True


def test_emergent_ignores_underscore_prefix_folders(tmp_path):
    # `_inbox`, `_sinteses`, and other underscore-prefixed system folders
    # are not theme folders. A theme named "inbox" should NOT match `_inbox`.
    (tmp_path / "_inbox").mkdir()
    (tmp_path / "_sinteses").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="inbox",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note", domain="inbox"),
        config=config,
    )
    assert result.destination == tmp_path / "_inbox"
    assert result.inbox_fallback is True


# ---------------------------------------------------------------------------
# Round 7: invariants - absolute paths, mode field, reason field
# ---------------------------------------------------------------------------


def test_fixed_domains_destination_is_absolute(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.destination.is_absolute()


def test_emergent_destination_is_absolute(tmp_path):
    (tmp_path / "consciencia").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="consciencia",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note"),
        config=config,
    )
    assert result.destination.is_absolute()


def test_mode_field_set_correctly_fixed_domains(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.mode == "fixed_domains"


def test_reason_field_is_non_empty_human_readable(tmp_path):
    config = _make_config(vault_path=tmp_path)
    result = route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert result.reason
    assert isinstance(result.reason, str)
    # Reason should reference key routing facts so audit logs are useful.
    assert "session" in result.reason.lower()


def test_reason_field_emergent_includes_theme(tmp_path):
    (tmp_path / "consciencia").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="consciencia",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note"),
        config=config,
    )
    assert "consciencia" in result.reason.lower()


# ---------------------------------------------------------------------------
# Round 8: filesystem purity - route does not create folders
# ---------------------------------------------------------------------------


def test_route_does_not_create_destination_folder(tmp_path):
    config = _make_config(vault_path=tmp_path)
    route(
        detection=_make_detection("session"),
        classification=_make_classification(),
        para=_make_para(category="area"),
        frontmatter=_make_frontmatter(note_type="session"),
        config=config,
    )
    assert not (tmp_path / "sessions").exists()


def test_route_does_not_create_inbox_folder(tmp_path):
    config = _make_config(mode="emergent", vault_path=tmp_path)
    route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="new-theme",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=None,
        frontmatter=_make_frontmatter(note_type="note"),
        config=config,
    )
    assert not (tmp_path / "_inbox").exists()


# ---------------------------------------------------------------------------
# Round 9: PARA contract enforcement
# ---------------------------------------------------------------------------


def test_emergent_mode_with_para_provided_still_routes_by_theme(tmp_path):
    # If a caller incorrectly passes para in emergent mode, the function
    # should ignore it (emergent does not consult PARA per spec line 205).
    (tmp_path / "consciencia").mkdir()
    config = _make_config(mode="emergent", vault_path=tmp_path)
    result = route(
        detection=_make_detection("note"),
        classification=ClassificationResult(
            primary="consciencia",
            secondary=(),
            confidence=0.7,
            uncertain=False,
            mode="emergent",
        ),
        para=_make_para(category="project", project_slug="ignored"),
        frontmatter=_make_frontmatter(note_type="note"),
        config=config,
    )
    assert result.destination == tmp_path / "consciencia"
    assert result.mode == "emergent"


def test_fixed_domains_mode_requires_para(tmp_path):
    config = _make_config(vault_path=tmp_path)
    with pytest.raises((ValueError, TypeError)):
        route(
            detection=_make_detection("session"),
            classification=_make_classification(),
            para=None,
            frontmatter=_make_frontmatter(note_type="session"),
            config=config,
        )
