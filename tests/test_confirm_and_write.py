"""Tests for the confirm_and_write entrypoint (post-confirmation file write).

`confirm_and_write` is the second half of the orchestrator pipeline: it
takes a dry-run `IntakeRun` (produced by `run_intake`) plus the user's
implicit confirmation (the CLI wrapper has already prompted), writes
the file to disk, re-invokes Step 9 against the written path, threads
any returned source_id back into frontmatter, and re-writes the file
with the updated YAML.

Phase 2 sign-off captured 2026-04-30 (Jon):

- Signature: `confirm_and_write(intake_run, config, *, nlm_command,
  overwrite=False) -> IntakeRun`. Mechanical, never prompts.
- File naming: `{frontmatter.title}.md` placed at `route.destination`
  for regular writes; `route.destination` IS the file path for
  section-update mode (existing project hub gets a section appended).
- Section-update on a missing destination raises `FileNotFoundError`
  per Q1 (route.is_section_update implies the project hub already
  exists semantically; if it does not, that is a vault-state surprise).
- Collision: `FileExistsError` by default, `overwrite=True` replaces.
  Section-update never collides because it always appends.
- Atomic write: temp file + os.replace, no temp leaks after success.
- Live Step 9: invoked with `note_path=<written>` for regular writes;
  on non-None source_id, frontmatter is mutated and the file is re-
  written atomically. In section-update mode, Step 9 is skipped to
  avoid duplicate NotebookLM sources for the same project hub.
- queued_nlm_count carry-forward + add live: original queue + 1 if
  live result.queued.
- Defense in depth: destination outside vault_path raises ValueError
  per spec safety rule 6.

The function lives in vault_intake.orchestrator alongside run_intake.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.detect import DetectionResult
from vault_intake.frontmatter import Frontmatter
from vault_intake.next_actions import NextActionsResult
from vault_intake.notebooklm import NotebookLMResult
from vault_intake.orchestrator import (
    IntakeRun,
    assemble_final_markdown,
    confirm_and_write,
)
from vault_intake.para import ParaResult
from vault_intake.refine import RefinedContent
from vault_intake.route import RouteResult


# ---------------------------------------------------------------------------
# Builders (mirror conventions in test_orchestrator.py)
# ---------------------------------------------------------------------------


def _make_config(
    *,
    vault_path: Path,
    notebook_map: dict[str, str] | None = None,
    skip_notebooklm: bool = True,
) -> Config:
    return Config(
        vault_path=vault_path,
        mode="fixed_domains",
        domains=(
            Domain(slug="ops", description="operations"),
            Domain(slug="dev", description="software"),
        ),
        notebook_map=MappingProxyType(notebook_map or {}),
        language="en",
        skip_notebooklm=skip_notebooklm,
        refinement_enabled=True,
        classification_confidence_threshold=0.6,
    )


def _make_vault(tmp_path: Path) -> Path:
    """Build a minimal fixed_domains vault with all standard folders."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for folder in (
        "sessions",
        "insights",
        "workflows",
        "prompts",
        "context",
        "projects",
        "references",
        "_inbox",
    ):
        (vault / folder).mkdir()
    return vault


def _make_frontmatter(
    *,
    title: str = "test-note",
    domain: str = "ops",
    type_: str = "session",
    source_id: str = "",
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
        type=type_,  # type: ignore[arg-type]
        domain=domain,
        tags=("ops",),
        notebook="",
        source_id=source_id,
        project="",
    )


def _make_detection() -> DetectionResult:
    return DetectionResult(
        type="session",
        uncertain=False,
        signals=(),
        refinement_applicable=False,
    )


def _make_classification() -> ClassificationResult:
    return ClassificationResult(
        primary="ops",
        secondary=(),
        confidence=0.8,
        uncertain=False,
        mode="fixed_domains",
    )


def _make_para_area() -> ParaResult:
    return ParaResult(
        category="area",
        project_slug=None,
        uncertain=False,
        signals=(),
    )


def _make_route_regular(vault: Path, *, folder: str = "sessions") -> RouteResult:
    return RouteResult(
        destination=vault / folder,
        project_link_target=None,
        archive_flagged=False,
        inbox_fallback=False,
        is_section_update=False,
        reason="test regular write",
        mode="fixed_domains",
    )


def _make_route_section_update(vault: Path, *, slug: str = "alpha-launch") -> RouteResult:
    project_file = vault / "projects" / f"{slug}.md"
    return RouteResult(
        destination=project_file,
        project_link_target=project_file,
        archive_flagged=False,
        inbox_fallback=False,
        is_section_update=True,
        reason="test section update",
        mode="fixed_domains",
    )


def _empty_next_actions() -> NextActionsResult:
    return NextActionsResult(proposals=(), gate_fired=False, signals_detected=())


def _make_intake_run(
    *,
    vault: Path,
    route_result: RouteResult,
    title: str = "test-note",
    body: str = "Hello world.\n\nThis is the body.",
    refinement: RefinedContent | None = None,
    notebooklm: NotebookLMResult | None = None,
    queued_nlm_count: int = 0,
) -> IntakeRun:
    fm = _make_frontmatter(title=title)
    next_actions = _empty_next_actions()
    final_md = assemble_final_markdown(
        body=body,
        frontmatter=fm,
        refinement=refinement,
        next_actions=next_actions,
    )
    return IntakeRun(
        detection=_make_detection(),
        refinement=refinement,
        classification=_make_classification(),
        para=_make_para_area(),
        frontmatter=fm,
        wikilinks=None,
        next_actions=next_actions,
        route=route_result,
        notebooklm=notebooklm
        or NotebookLMResult(
            source_id=None,
            notebook_id=None,
            skipped=True,
            failed=False,
            queued=False,
            reason="dry-run: no note_path provided",
            source_count_warning=False,
        ),
        final_markdown=final_md,
        written_path=None,
        queued_nlm_count=queued_nlm_count,
        questions=(),
    )


# ---------------------------------------------------------------------------
# Regular write
# ---------------------------------------------------------------------------


class TestConfirmAndWriteRegular:
    def test_writes_file_at_destination(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault, folder="sessions"),
            title="hello-world",
        )
        result = confirm_and_write(intake_run, config)
        expected = vault / "sessions" / "hello-world.md"
        assert expected.is_file()
        assert result.written_path == expected

    def test_file_content_matches_final_markdown(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="content-check",
        )
        result = confirm_and_write(intake_run, config)
        on_disk = result.written_path.read_text(encoding="utf-8")
        assert on_disk == intake_run.final_markdown

    def test_returns_new_intake_run_with_written_path_set(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="check-return",
        )
        result = confirm_and_write(intake_run, config)
        assert result is not intake_run
        assert result.written_path is not None
        assert intake_run.written_path is None

    def test_creates_destination_folder_when_missing(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        nested_folder = vault / "sessions" / "subfolder"
        # Folder does not exist yet; route points into it.
        route = RouteResult(
            destination=nested_folder,
            project_link_target=None,
            archive_flagged=False,
            inbox_fallback=False,
            is_section_update=False,
            reason="nested folder",
            mode="fixed_domains",
        )
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=route,
            title="nested",
        )
        result = confirm_and_write(intake_run, config)
        assert result.written_path == nested_folder / "nested.md"
        assert result.written_path.is_file()


# ---------------------------------------------------------------------------
# Collision handling
# ---------------------------------------------------------------------------


class TestConfirmAndWriteCollision:
    def test_raises_file_exists_error_when_target_exists_and_no_overwrite(
        self, tmp_path: Path
    ):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="dup",
        )
        existing = vault / "sessions" / "dup.md"
        existing.write_text("pre-existing content", encoding="utf-8")
        with pytest.raises(FileExistsError):
            confirm_and_write(intake_run, config)
        # Existing file untouched.
        assert existing.read_text(encoding="utf-8") == "pre-existing content"

    def test_overwrite_true_replaces_existing(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="dup",
            body="new content",
        )
        existing = vault / "sessions" / "dup.md"
        existing.write_text("old content", encoding="utf-8")
        result = confirm_and_write(intake_run, config, overwrite=True)
        assert result.written_path == existing
        assert "new content" in existing.read_text(encoding="utf-8")
        assert "old content" not in existing.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Section-update path
# ---------------------------------------------------------------------------


class TestConfirmAndWriteSectionUpdate:
    def test_appends_section_to_existing_project_hub(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        slug = "alpha-launch"
        project_file = vault / "projects" / f"{slug}.md"
        project_file.write_text(
            "---\ntitle: alpha-launch\ntype: project\n---\n\n# alpha-launch\n\nOriginal content.\n",
            encoding="utf-8",
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_section_update(vault, slug=slug),
            title="kickoff-decisions",
            body="Decided to ship before EOM.",
        )
        result = confirm_and_write(intake_run, config)
        assert result.written_path == project_file
        on_disk = project_file.read_text(encoding="utf-8")
        # Original content preserved at the top.
        assert "Original content." in on_disk
        # New section heading appears.
        assert "## kickoff-decisions" in on_disk
        # New body appears below the section heading.
        kickoff_pos = on_disk.index("## kickoff-decisions")
        body_pos = on_disk.index("Decided to ship before EOM.")
        assert body_pos > kickoff_pos

    def test_section_includes_captura_when_refinement_changed(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        slug = "beta-launch"
        project_file = vault / "projects" / f"{slug}.md"
        project_file.write_text(
            "---\ntitle: beta-launch\n---\n\nSeed.\n",
            encoding="utf-8",
        )
        refinement = RefinedContent(
            refined="Cleaned text.",
            original="Original raw text.",
            changed=True,
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_section_update(vault, slug=slug),
            title="raw-paste",
            body="Cleaned text.",
            refinement=refinement,
        )
        confirm_and_write(intake_run, config)
        on_disk = project_file.read_text(encoding="utf-8")
        assert "## Captura original" in on_disk
        assert "Original raw text." in on_disk

    def test_raises_file_not_found_when_section_target_missing(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_section_update(vault, slug="never-created"),
            title="orphan",
        )
        with pytest.raises(FileNotFoundError):
            confirm_and_write(intake_run, config)

    def test_section_update_skips_live_step9(self, tmp_path: Path):
        # Section-update should not re-invoke integrate_notebooklm; the
        # project hub may already be a NotebookLM source and re-adding
        # would create duplicates. The returned IntakeRun's notebooklm
        # field carries a skipped result with a section-update reason.
        vault = _make_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            notebook_map={"ops": "nb-ops-id"},
            skip_notebooklm=False,
        )
        slug = "gamma-launch"
        project_file = vault / "projects" / f"{slug}.md"
        project_file.write_text(
            "---\ntitle: gamma-launch\n---\n\nSeed.\n", encoding="utf-8"
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_section_update(vault, slug=slug),
            title="status",
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm"
        ) as mock_step9:
            result = confirm_and_write(intake_run, config)
        assert mock_step9.call_count == 0
        assert result.notebooklm is not None
        assert result.notebooklm.skipped is True


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


class TestConfirmAndWriteAtomic:
    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="atomic-check",
        )
        confirm_and_write(intake_run, config)
        leftover = list((vault / "sessions").glob("*.tmp"))
        assert leftover == []

    def test_no_temp_file_left_after_overwrite(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="atomic-overwrite",
        )
        target = vault / "sessions" / "atomic-overwrite.md"
        target.write_text("old", encoding="utf-8")
        confirm_and_write(intake_run, config, overwrite=True)
        leftover = list((vault / "sessions").glob("*.tmp"))
        assert leftover == []


# ---------------------------------------------------------------------------
# Live Step 9 (mocked integrate_notebooklm)
# ---------------------------------------------------------------------------


def _mock_step9_success(source_id: str = "src-LIVE"):
    return NotebookLMResult(
        source_id=source_id,
        notebook_id="nb-ops-id",
        skipped=False,
        failed=False,
        queued=False,
        reason=f"added to nb-ops-id",
        source_count_warning=False,
    )


def _mock_step9_auth_recoverable():
    return NotebookLMResult(
        source_id=None,
        notebook_id="nb-ops-id",
        skipped=False,
        failed=True,
        queued=True,
        reason="auth precheck failed: cookies expired",
        source_count_warning=False,
    )


def _mock_step9_non_auth_failure():
    return NotebookLMResult(
        source_id=None,
        notebook_id="nb-ops-id",
        skipped=False,
        failed=True,
        queued=False,
        reason="source list timeout",
        source_count_warning=False,
    )


def _mock_step9_skipped():
    return NotebookLMResult(
        source_id=None,
        notebook_id=None,
        skipped=True,
        failed=False,
        queued=False,
        reason="no mapping for classification key 'ops'",
        source_count_warning=False,
    )


class TestConfirmAndWriteLiveStep9:
    def test_live_invoked_with_written_note_path(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            notebook_map={"ops": "nb-ops-id"},
            skip_notebooklm=False,
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="live-step9-path",
        )
        captured: dict = {}

        def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return _mock_step9_skipped()

        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            side_effect=_capture,
        ):
            result = confirm_and_write(intake_run, config)
        assert "kwargs" in captured
        # note_path must be the written file path, not None.
        assert captured["kwargs"].get("note_path") == result.written_path

    def test_source_id_mutates_frontmatter_and_re_writes_file(
        self, tmp_path: Path
    ):
        vault = _make_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            notebook_map={"ops": "nb-ops-id"},
            skip_notebooklm=False,
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="mutation-rewrite",
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            return_value=_mock_step9_success(source_id="src-LIVE-123"),
        ):
            result = confirm_and_write(intake_run, config)
        # Returned IntakeRun reflects the mutation.
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == "src-LIVE-123"
        # File on disk reflects the mutation.
        on_disk = result.written_path.read_text(encoding="utf-8")
        assert "source_id: src-LIVE-123" in on_disk
        # final_markdown re-rendered with new YAML.
        assert result.final_markdown == on_disk

    def test_auth_recoverable_failure_keeps_file_and_increments_queue(
        self, tmp_path: Path
    ):
        vault = _make_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            notebook_map={"ops": "nb-ops-id"},
            skip_notebooklm=False,
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="auth-fail",
            queued_nlm_count=2,  # carry-forward from prelude residue
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            return_value=_mock_step9_auth_recoverable(),
        ):
            result = confirm_and_write(intake_run, config)
        # File still written despite Step 9 failure.
        assert result.written_path.is_file()
        # Frontmatter unchanged (no source_id).
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == ""
        # Queue carries forward + 1 for live queued result.
        assert result.queued_nlm_count == 3
        assert result.notebooklm is not None
        assert result.notebooklm.queued is True

    def test_non_auth_failure_keeps_file_no_queue_increment(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            notebook_map={"ops": "nb-ops-id"},
            skip_notebooklm=False,
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="non-auth-fail",
            queued_nlm_count=1,
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            return_value=_mock_step9_non_auth_failure(),
        ):
            result = confirm_and_write(intake_run, config)
        assert result.written_path.is_file()
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == ""
        # Queue not incremented (queued=False on non-auth failure).
        assert result.queued_nlm_count == 1
        assert result.notebooklm is not None
        assert result.notebooklm.failed is True
        assert result.notebooklm.queued is False

    def test_skipped_step9_keeps_file_unchanged(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault, skip_notebooklm=True)
        intake_run = _make_intake_run(
            vault=vault,
            route_result=_make_route_regular(vault),
            title="skipped-step9",
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            return_value=_mock_step9_skipped(),
        ):
            result = confirm_and_write(intake_run, config)
        assert result.written_path.is_file()
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == ""
        # Step 9 returned skipped; no re-write, queue unchanged.
        assert result.queued_nlm_count == 0


# ---------------------------------------------------------------------------
# Defense in depth (spec safety rule 6)
# ---------------------------------------------------------------------------


class TestConfirmAndWriteSafety:
    def test_destination_outside_vault_raises_value_error(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        rogue_destination = tmp_path / "elsewhere"
        rogue_destination.mkdir()
        rogue_route = RouteResult(
            destination=rogue_destination,
            project_link_target=None,
            archive_flagged=False,
            inbox_fallback=False,
            is_section_update=False,
            reason="rogue",
            mode="fixed_domains",
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=rogue_route,
            title="rogue",
        )
        with pytest.raises(ValueError, match=r"vault_path"):
            confirm_and_write(intake_run, config)

    def test_section_update_destination_outside_vault_raises_value_error(
        self, tmp_path: Path
    ):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        rogue_file = tmp_path / "elsewhere.md"
        rogue_file.write_text("rogue", encoding="utf-8")
        rogue_route = RouteResult(
            destination=rogue_file,
            project_link_target=rogue_file,
            archive_flagged=False,
            inbox_fallback=False,
            is_section_update=True,
            reason="rogue section update",
            mode="fixed_domains",
        )
        intake_run = _make_intake_run(
            vault=vault,
            route_result=rogue_route,
            title="rogue-section",
        )
        with pytest.raises(ValueError, match=r"vault_path"):
            confirm_and_write(intake_run, config)


# ---------------------------------------------------------------------------
# IntakeRun shape: the function must not require a route to write
# ---------------------------------------------------------------------------


class TestConfirmAndWritePreconditions:
    def test_raises_when_intakerun_has_no_route(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        # IntakeRun built without a route (Step 8 was skipped).
        fm = _make_frontmatter()
        next_actions = _empty_next_actions()
        run = IntakeRun(
            detection=_make_detection(),
            refinement=None,
            classification=_make_classification(),
            para=_make_para_area(),
            frontmatter=fm,
            wikilinks=None,
            next_actions=next_actions,
            route=None,
            notebooklm=None,
            final_markdown=assemble_final_markdown(
                body="body", frontmatter=fm, refinement=None, next_actions=next_actions
            ),
            written_path=None,
            queued_nlm_count=0,
            questions=(),
        )
        with pytest.raises(ValueError, match=r"route"):
            confirm_and_write(run, config)

    def test_raises_when_intakerun_has_no_frontmatter(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        config = _make_config(vault_path=vault)
        run = IntakeRun(
            detection=_make_detection(),
            refinement=None,
            classification=_make_classification(),
            para=_make_para_area(),
            frontmatter=None,
            wikilinks=None,
            next_actions=_empty_next_actions(),
            route=_make_route_regular(vault),
            notebooklm=None,
            final_markdown="",
            written_path=None,
            queued_nlm_count=0,
            questions=(),
        )
        with pytest.raises(ValueError, match=r"frontmatter"):
            confirm_and_write(run, config)
