"""Tests for the vault-intake orchestrator (build spec lines 228-243).

The orchestrator wires Steps 0 through 9 together and produces a
structured `IntakeRun` plus a final-markdown body assembled per the
spec's output contract. This module is the first commit milestone of
the orchestrator session: dry-run only (no file writes), fixed_domains
mode primary, emergent mode caught-and-surfaced when downstream steps
raise NotImplementedError.

Phase 1 sign-off captured 2026-04-30:

- Single Python entrypoint `run_intake(input_text, config, ...) -> IntakeRun`.
- Frozen `IntakeRun` dataclass per the kickoff item 2 shape.
- Pipeline ordering and gating per kickoff item 3: 0 → 1 → 2 (gated) →
  3 → 4 (skip in emergent) → 5 (skip in emergent v1) → 6 (skip in
  emergent v1) → 7 (mode-agnostic) → 8 (both modes) → 9 (gate on
  skip_notebooklm and mapping).
- Final markdown assembly per kickoff item 4: frontmatter + body +
  optional `## Possíveis próximos passos` + optional `## Captura original`.
- Frontmatter mutations owned by orchestrator via `dataclasses.replace`.
- File-write contract: dry-run only in this commit; `confirm_and_write`
  lands in a follow-up commit.
- Uncertainty escalations collected into `IntakeRun.questions`.
- Auto-drain of the NotebookLM retry queue at every run start; result
  contributes to `queued_nlm_count`.

Mode-agnostic Step 7 always runs. Steps 3-6 wrap in try/except
NotImplementedError so emergent-mode runs degrade gracefully into a
partial IntakeRun with `questions` documenting what was skipped.
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
from vault_intake.next_actions import NextAction, NextActionsResult
from vault_intake.notebooklm import FlushResult, NotebookLMResult
from vault_intake.orchestrator import (
    IntakeRun,
    assemble_final_markdown,
    collect_questions,
    run_intake,
)
from vault_intake.para import ParaResult
from vault_intake.refine import RefinedContent
from vault_intake.route import RouteResult
from vault_intake.wikilinks import Wikilink, WikilinkResult


# ---------------------------------------------------------------------------
# Vault builders
# ---------------------------------------------------------------------------


def _make_fixed_domains_vault(
    tmp_path: Path,
    *,
    project_slugs: tuple[str, ...] = (),
    sibling_notes: tuple[tuple[str, str, str | None], ...] = (),
) -> Path:
    """Build a minimal fixed_domains vault in tmp_path.

    Creates standard fixed_domains folders, optional project files
    under projects/, and optional sibling notes (filename, title,
    domain) so wikilink walks have something to consider.
    """
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

    for slug in project_slugs:
        project_md = vault / "projects" / f"{slug}.md"
        project_md.write_text(
            f"---\ntitle: {slug}\ntype: project\n---\n# {slug}\n",
            encoding="utf-8",
        )

    for filename, title, domain in sibling_notes:
        note = vault / "sessions" / filename
        domain_line = f"\ndomain: {domain}" if domain else ""
        note.write_text(
            f"---\ntitle: {title}\ntype: session{domain_line}\n---\n# {title}\n",
            encoding="utf-8",
        )

    return vault


def _make_emergent_vault(tmp_path: Path, *, theme_folders: tuple[str, ...] = ()) -> Path:
    """Build a minimal emergent vault: `_inbox/`, `_sinteses/`, plus theme folders.

    Per kickoff item 10 sub-decision: minimal structure to verify
    emergent-mode runs through orchestrator and surface NotImplemented
    skips as questions rather than crashing.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "_inbox").mkdir()
    (vault / "_sinteses").mkdir()
    for theme in theme_folders:
        (vault / theme).mkdir()
    return vault


def _make_config(
    *,
    vault_path: Path,
    mode: str = "fixed_domains",
    domains: tuple[Domain, ...] | None = None,
    notebook_map: dict[str, str] | None = None,
    skip_notebooklm: bool = True,
    refinement_enabled: bool = True,
    classification_confidence_threshold: float = 0.6,
) -> Config:
    if domains is None:
        if mode == "fixed_domains":
            domains = (
                Domain(slug="ops", description="operations processes infrastructure"),
                Domain(slug="branding", description="brand identity design messaging"),
                Domain(slug="dev", description="software engineering code testing"),
            )
        else:
            domains = ()
    return Config(
        vault_path=vault_path,
        mode=mode,  # type: ignore[arg-type]
        domains=domains,
        notebook_map=MappingProxyType(notebook_map or {}),
        language="en",
        skip_notebooklm=skip_notebooklm,
        refinement_enabled=refinement_enabled,
        classification_confidence_threshold=classification_confidence_threshold,
    )


def _empty_flush() -> FlushResult:
    return FlushResult(processed=0, still_queued=0, dropped=0)


# ---------------------------------------------------------------------------
# IntakeRun shape
# ---------------------------------------------------------------------------


class TestIntakeRunShape:
    def test_intakerun_is_frozen(self):
        run = IntakeRun(
            detection=DetectionResult(type="note", uncertain=False, signals=(), refinement_applicable=False),
            refinement=None,
            classification=None,
            para=None,
            frontmatter=None,
            wikilinks=None,
            next_actions=NextActionsResult(proposals=(), gate_fired=False, signals_detected=()),
            route=None,
            notebooklm=None,
            body="",
            final_markdown="",
            written_path=None,
            queued_nlm_count=0,
            questions=(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            run.queued_nlm_count = 5  # type: ignore[misc]

    def test_intakerun_has_expected_fields(self):
        names = {f.name for f in dataclasses.fields(IntakeRun)}
        assert names == {
            "detection",
            "refinement",
            "classification",
            "para",
            "frontmatter",
            "wikilinks",
            "next_actions",
            "route",
            "notebooklm",
            "body",
            "final_markdown",
            "written_path",
            "queued_nlm_count",
            "questions",
        }


# ---------------------------------------------------------------------------
# assemble_final_markdown helper
# ---------------------------------------------------------------------------


def _make_frontmatter(
    *,
    title: str = "test-note",
    domain: str = "ops",
    type_: str = "session",
    source_id: str = "",
    original_ref: str = "",
) -> Frontmatter:
    return Frontmatter(
        schema_version="1.0",
        source_type="paste",
        source_uri="",
        captured_at="2026-04-30",
        processed_by="/vault-intake",
        confidence=0.8,
        original_ref=original_ref,
        title=title,
        date="2026-04-30",
        type=type_,  # type: ignore[arg-type]
        domain=domain,
        tags=("ops",),
        notebook="",
        source_id=source_id,
        project="",
    )


def _make_next_actions_empty() -> NextActionsResult:
    return NextActionsResult(proposals=(), gate_fired=False, signals_detected=())


def _make_next_actions_one() -> NextActionsResult:
    return NextActionsResult(
        proposals=(
            NextAction(
                what="Send the deck to Alice tomorrow.",
                when="tomorrow",
                where="Alice",
                effort=None,
                waiting_on=None,
                signal="date + imperative + named_followup",
                source_excerpt="Send the deck to Alice tomorrow.",
            ),
        ),
        gate_fired=True,
        signals_detected=("date", "imperative", "named_followup"),
    )


class TestAssembleFinalMarkdown:
    def test_minimal_assembly_no_refinement_no_next_actions(self):
        fm = _make_frontmatter()
        body = "Hello world.\n\nThis is the body."
        out = assemble_final_markdown(
            body=body,
            frontmatter=fm,
            refinement=None,
            next_actions=_make_next_actions_empty(),
        )
        assert out.startswith("---\n")
        assert "title: test-note" in out
        assert "Hello world." in out
        assert "## Captura original" not in out
        assert "## Possíveis próximos passos" not in out

    def test_assembly_with_next_actions(self):
        out = assemble_final_markdown(
            body="Body text here.",
            frontmatter=_make_frontmatter(),
            refinement=None,
            next_actions=_make_next_actions_one(),
        )
        assert "## Possíveis próximos passos" in out
        assert "Send the deck to Alice tomorrow." in out

    def test_assembly_with_refinement_and_changed(self):
        original = "Tipo, isso e tipo, aquilo."
        refinement = RefinedContent(
            refined="Isso, aquilo.",
            original=original,
            changed=True,
        )
        out = assemble_final_markdown(
            body="Isso, aquilo.",
            frontmatter=_make_frontmatter(),
            refinement=refinement,
            next_actions=_make_next_actions_empty(),
        )
        assert "## Captura original" in out
        assert original in out

    def test_assembly_omits_captura_when_refinement_unchanged(self):
        refinement = RefinedContent(
            refined="Already clean text.",
            original="Already clean text.",
            changed=False,
        )
        out = assemble_final_markdown(
            body="Already clean text.",
            frontmatter=_make_frontmatter(),
            refinement=refinement,
            next_actions=_make_next_actions_empty(),
        )
        assert "## Captura original" not in out

    def test_source_id_renders_into_yaml_block(self):
        fm = _make_frontmatter(source_id="src-XYZ")
        out = assemble_final_markdown(
            body="Body.",
            frontmatter=fm,
            refinement=None,
            next_actions=_make_next_actions_empty(),
        )
        assert "source_id: src-XYZ" in out

    def test_yaml_block_parses_back_to_dict(self):
        # Locks the `---\n{yaml}---` delimiter pattern: the assembled YAML
        # must round-trip through yaml.safe_load. Codex review T
        # "FINAL_MARKDOWN_YAML_NOT_REGRESSION_LOCKED" 2026-04-30.
        import re

        import yaml

        fm = _make_frontmatter(source_id="src-roundtrip", title="my-note")
        out = assemble_final_markdown(
            body="Body line.",
            frontmatter=fm,
            refinement=None,
            next_actions=_make_next_actions_empty(),
        )
        match = re.match(r"^---\n(.*?)\n---\n", out, re.DOTALL)
        assert match is not None, f"no frontmatter block in: {out!r}"
        parsed = yaml.safe_load(match.group(1))
        assert isinstance(parsed, dict)
        assert parsed["title"] == "my-note"
        assert parsed["source_id"] == "src-roundtrip"
        assert parsed["schema_version"] == "1.0"
        assert parsed["domain"] == "ops"
        # Lock the rest of the OS-wide baseline plus fixed_domains additions
        # against delimiter/serialization regressions. Codex confirmation pass
        # T1 PARTIAL 2026-04-30.
        assert parsed["date"] == "2026-04-30"
        assert parsed["type"] == "session"
        assert parsed["tags"] == ["ops"]
        assert parsed["notebook"] == ""
        assert parsed["captured_at"] == "2026-04-30"
        assert parsed["processed_by"] == "/vault-intake"


# ---------------------------------------------------------------------------
# collect_questions helper
# ---------------------------------------------------------------------------


def _make_detection(*, content_type: str = "note", uncertain: bool = False) -> DetectionResult:
    return DetectionResult(
        type=content_type,  # type: ignore[arg-type]
        uncertain=uncertain,
        signals=(),
        refinement_applicable=False,
    )


def _make_classification(
    *,
    primary: str = "ops",
    uncertain: bool = False,
    mode: str = "fixed_domains",
) -> ClassificationResult:
    return ClassificationResult(
        primary=primary,
        secondary=(),
        confidence=0.8 if not uncertain else 0.3,
        uncertain=uncertain,
        mode=mode,  # type: ignore[arg-type]
    )


def _make_para(*, category: str = "area", uncertain: bool = False) -> ParaResult:
    return ParaResult(
        category=category,  # type: ignore[arg-type]
        project_slug=None,
        uncertain=uncertain,
        signals=(),
    )


def _make_route(*, archive_flagged: bool = False) -> RouteResult:
    return RouteResult(
        destination=Path("/tmp/dest"),
        project_link_target=None,
        archive_flagged=archive_flagged,
        inbox_fallback=False,
        is_section_update=False,
        reason="test",
        mode="fixed_domains",
    )


class TestCollectQuestions:
    def test_no_questions_when_nothing_uncertain(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=_make_classification(),
            para=_make_para(),
            route=_make_route(),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        # Title heuristic is always confirmed per spec line 153.
        assert any("Title" in q.prompt for q in questions)
        assert not any("detected" in q.prompt.lower() for q in questions)

    def test_detection_uncertain_emits_question(self):
        questions = collect_questions(
            detection=_make_detection(uncertain=True, content_type="prompt"),
            classification=_make_classification(),
            para=_make_para(),
            route=_make_route(),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        assert any("`prompt`" in q.prompt for q in questions)

    def test_classification_uncertain_emits_question(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=_make_classification(uncertain=True, primary="branding"),
            para=_make_para(),
            route=_make_route(),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        assert any("`branding`" in q.prompt for q in questions)

    def test_para_uncertain_emits_question(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=_make_classification(),
            para=_make_para(uncertain=True, category="archive"),
            route=_make_route(),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        assert any("`archive`" in q.prompt for q in questions)

    def test_archive_flagged_emits_question(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=_make_classification(),
            para=_make_para(),
            route=_make_route(archive_flagged=True),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        assert any("archive" in q.prompt.lower() for q in questions)

    def test_not_implemented_steps_emit_questions(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=None,
            para=None,
            route=None,
            frontmatter=None,
            not_implemented=("classify", "categorize_para"),
        )
        assert any("classify" in q.prompt for q in questions)
        assert any("categorize_para" in q.prompt for q in questions)

    def test_no_title_question_when_frontmatter_missing(self):
        questions = collect_questions(
            detection=_make_detection(),
            classification=None,
            para=None,
            route=None,
            frontmatter=None,
            not_implemented=("classify",),
        )
        assert not any("Title" in q.prompt for q in questions)


# ---------------------------------------------------------------------------
# IntakeQuestion structured-question shape (CLI wrapper enabling refactor)
# ---------------------------------------------------------------------------


class TestIntakeQuestionShape:
    """Lock the structured `IntakeQuestion` shape that the CLI wrapper
    needs in order to route answers programmatically.

    The free-form string `tuple[str, ...]` shape used by the dry-run
    orchestrator session is replaced here with `tuple[IntakeQuestion, ...]`
    so the CLI wrapper can dispatch each answer to the right field via a
    `kind` enum rather than parsing the question text.
    """

    def test_question_kind_enum_values(self):
        from vault_intake.orchestrator import QuestionKind
        assert QuestionKind.DETECTION_TYPE.value == "detection.type"
        assert QuestionKind.CLASSIFICATION.value == "classification.primary"
        assert QuestionKind.PARA.value == "para.category"
        assert QuestionKind.ROUTE_ARCHIVE.value == "route.archive"
        assert QuestionKind.FRONTMATTER_TITLE.value == "frontmatter.title"
        assert QuestionKind.NOT_IMPLEMENTED.value == "not_implemented"

    def test_intake_question_is_frozen(self):
        from vault_intake.orchestrator import IntakeQuestion, QuestionKind
        q = IntakeQuestion(
            kind=QuestionKind.DETECTION_TYPE,
            prompt="I detected this as `prompt`; correct?",
            suggested="prompt",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            q.prompt = "changed"  # type: ignore[misc]

    def test_collect_questions_returns_intake_question_tuple(self):
        from vault_intake.orchestrator import IntakeQuestion
        questions = collect_questions(
            detection=_make_detection(uncertain=True, content_type="prompt"),
            classification=_make_classification(uncertain=True, primary="branding"),
            para=_make_para(uncertain=True, category="archive"),
            route=_make_route(archive_flagged=True),
            frontmatter=_make_frontmatter(),
            not_implemented=("classify",),
        )
        assert isinstance(questions, tuple)
        assert all(isinstance(q, IntakeQuestion) for q in questions)

    def test_collect_questions_emits_one_kind_per_uncertainty(self):
        from vault_intake.orchestrator import QuestionKind
        questions = collect_questions(
            detection=_make_detection(uncertain=True, content_type="prompt"),
            classification=_make_classification(uncertain=True, primary="branding"),
            para=_make_para(uncertain=True, category="archive"),
            route=_make_route(archive_flagged=True),
            frontmatter=_make_frontmatter(),
            not_implemented=("classify", "categorize_para"),
        )
        kinds = [q.kind for q in questions]
        assert QuestionKind.DETECTION_TYPE in kinds
        assert QuestionKind.CLASSIFICATION in kinds
        assert QuestionKind.PARA in kinds
        assert QuestionKind.ROUTE_ARCHIVE in kinds
        assert QuestionKind.FRONTMATTER_TITLE in kinds
        # NOT_IMPLEMENTED can repeat (one per skipped step).
        not_impl = [q for q in questions if q.kind == QuestionKind.NOT_IMPLEMENTED]
        assert len(not_impl) == 2

    def test_suggested_carries_current_value(self):
        from vault_intake.orchestrator import QuestionKind
        questions = collect_questions(
            detection=_make_detection(uncertain=True, content_type="prompt"),
            classification=_make_classification(uncertain=True, primary="branding"),
            para=_make_para(uncertain=True, category="archive"),
            route=_make_route(),
            frontmatter=_make_frontmatter(title="my-note"),
            not_implemented=(),
        )
        by_kind = {q.kind: q for q in questions}
        assert by_kind[QuestionKind.DETECTION_TYPE].suggested == "prompt"
        assert by_kind[QuestionKind.CLASSIFICATION].suggested == "branding"
        assert by_kind[QuestionKind.PARA].suggested == "archive"
        assert by_kind[QuestionKind.FRONTMATTER_TITLE].suggested == "my-note"

    def test_route_archive_suggested_is_destination(self):
        from vault_intake.orchestrator import QuestionKind
        questions = collect_questions(
            detection=_make_detection(),
            classification=_make_classification(),
            para=_make_para(category="archive"),
            route=_make_route(archive_flagged=True),
            frontmatter=_make_frontmatter(),
            not_implemented=(),
        )
        by_kind = {q.kind: q for q in questions}
        assert by_kind[QuestionKind.ROUTE_ARCHIVE].suggested == str(Path("/tmp/dest"))

    def test_not_implemented_question_carries_step_field(self):
        from vault_intake.orchestrator import QuestionKind
        questions = collect_questions(
            detection=_make_detection(),
            classification=None,
            para=None,
            route=None,
            frontmatter=None,
            not_implemented=("classify", "categorize_para"),
        )
        not_impl = [q for q in questions if q.kind == QuestionKind.NOT_IMPLEMENTED]
        steps = [q.step for q in not_impl]
        assert steps == ["classify", "categorize_para"]
        # informational only; no value to suggest
        assert all(q.suggested is None for q in not_impl)

    def test_intake_run_questions_field_holds_intake_questions(self, tmp_path):
        """End-to-end `run_intake` returns an IntakeRun whose
        `questions` tuple is structured."""
        from vault_intake.orchestrator import IntakeQuestion
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert isinstance(result.questions, tuple)
        assert all(isinstance(q, IntakeQuestion) for q in result.questions)

    def test_summary_renders_question_prompts(self, tmp_path):
        """`IntakeRun.summary()` keeps the rendered string-per-line
        format. Question prompts surface verbatim under the
        `Confirmations needed:` heading."""
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        summary = result.summary()
        for question in result.questions:
            assert question.prompt in summary


# ---------------------------------------------------------------------------
# run_intake: golden path (fixed_domains)
# ---------------------------------------------------------------------------


_OPS_INPUT = """# Ops infra check

Quick ops note about infrastructure deployment process.
We need to verify the ops processes for infrastructure rollout.
"""


class TestRunIntakeGoldenPath:
    def test_golden_path_returns_intakerun(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert isinstance(result, IntakeRun)

    def test_golden_path_routes_to_sessions(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        # Document classifies as "note" via document signal; PARA=area;
        # spec table puts (note, area) -> sessions/.
        assert result.route is not None
        assert result.route.destination == vault / "sessions"

    def test_golden_path_classifies_to_ops(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert result.classification is not None
        assert result.classification.primary == "ops"

    def test_golden_path_dry_run_no_write(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert result.written_path is None

    def test_golden_path_assembles_final_markdown(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert result.final_markdown.startswith("---\n")
        assert "domain: ops" in result.final_markdown
        assert "Ops infra check" in result.final_markdown

    def test_golden_path_step9_dry_run_skipped(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, skip_notebooklm=False, notebook_map={"ops": "nb-ops"})
        result = run_intake(_OPS_INPUT, config)
        # In run_intake, Step 9 always runs as dry-run (note_path=None);
        # integrate_notebooklm returns _skipped("dry-run...").
        assert result.notebooklm is not None
        assert result.notebooklm.skipped is True
        assert "dry-run" in result.notebooklm.reason


# ---------------------------------------------------------------------------
# run_intake: refinement gate
# ---------------------------------------------------------------------------


def _build_transcription(extra_words: int = 350) -> str:
    """Build a transcription that crosses the 300-word + connectives gate.

    No markdown headings, includes Portuguese connectives, exceeds 300 words.
    """
    body = "Então eu acho que esse projeto tipo, e aí, e a gente vai precisar de mais foco. "
    filler = "E mais coisa para fazer, então também e tipo bastante trabalho. " * 50
    return body + filler


class TestRunIntakeRefinementGate:
    def test_refinement_runs_when_enabled_and_applicable(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, refinement_enabled=True)
        text = _build_transcription()
        result = run_intake(text, config)
        assert result.refinement is not None

    def test_refinement_skipped_when_config_disabled(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, refinement_enabled=False)
        text = _build_transcription()
        result = run_intake(text, config)
        assert result.refinement is None

    def test_refinement_skipped_when_detection_already_structured(self, tmp_path):
        # Document with markdown headings: refinement_applicable=False
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, refinement_enabled=True)
        text = "# Doc title\n\n## Section\n\nContent here."
        result = run_intake(text, config)
        assert result.refinement is None

    def test_captura_emitted_when_refinement_changed(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, refinement_enabled=True)
        text = _build_transcription()
        result = run_intake(text, config)
        # Refinement should change at least filler ("tipo", "aí", "né")
        assert result.refinement is not None
        assert result.refinement.changed is True
        assert "## Captura original" in result.final_markdown


# ---------------------------------------------------------------------------
# run_intake: PARA-project override
# ---------------------------------------------------------------------------


class TestRunIntakeParaProjectOverride:
    def test_project_override_routes_with_link(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path, project_slugs=("launch-redesign",))
        config = _make_config(vault_path=vault)
        # Plain text (no markdown H1) keeps detection.type="note", which
        # under the PARA-project override routes to sessions/ + link per
        # spec line 192. Word count is intentionally below the 20-word
        # brain-dump threshold so refinement does not interfere.
        text = (
            "Working on launch-redesign ops project today. "
            "Deploying ops infrastructure for launch-redesign work."
        )
        result = run_intake(text, config)
        assert result.para is not None
        assert result.para.category == "project"
        assert result.para.project_slug == "launch-redesign"
        assert result.frontmatter is not None
        assert result.frontmatter.type == "project"
        assert result.frontmatter.project == "launch-redesign"
        assert result.route is not None
        assert result.route.destination == vault / "sessions"
        assert result.route.project_link_target == vault / "projects" / "launch-redesign.md"


# ---------------------------------------------------------------------------
# run_intake: NotebookLM queue surface
# ---------------------------------------------------------------------------


class TestRunIntakeNotebookLMQueueSurface:
    def test_queued_count_reflects_flush_still_queued(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        with patch(
            "vault_intake.orchestrator.flush_nlm_queue",
            return_value=FlushResult(processed=0, still_queued=3, dropped=0),
        ):
            result = run_intake(_OPS_INPUT, config)
        assert result.queued_nlm_count == 3

    def test_queued_count_zero_when_queue_empty(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        # No mock; actual flush_nlm_queue runs against a vault with no queue dir.
        result = run_intake(_OPS_INPUT, config)
        assert result.queued_nlm_count == 0

    def test_queued_count_survives_flush_exception(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        with patch(
            "vault_intake.orchestrator.flush_nlm_queue",
            side_effect=RuntimeError("subprocess died"),
        ):
            result = run_intake(_OPS_INPUT, config)
        # Auto-drain failure must never break the run; queued_nlm_count
        # falls back to 0 for that source.
        assert result.queued_nlm_count == 0


# ---------------------------------------------------------------------------
# run_intake: uncertainty signals
# ---------------------------------------------------------------------------


class TestRunIntakeUncertainty:
    def test_uncertain_classification_appears_in_questions(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        # Input with zero domain-keyword hits scores 0.0 confidence, well
        # below the 0.6 default threshold; classify defaults primary to
        # the first-listed domain and flips uncertain=True.
        text = "Just a quick thought I jotted down."
        result = run_intake(text, config)
        assert result.classification is not None
        assert result.classification.uncertain is True
        assert any("classified" in q.prompt.lower() for q in result.questions)

    def test_title_question_always_present_when_frontmatter_built(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        assert any("Title" in q.prompt for q in result.questions)


# ---------------------------------------------------------------------------
# run_intake: frontmatter mutation flow
# ---------------------------------------------------------------------------


class TestRunIntakeFrontmatterMutation:
    def test_source_id_threaded_back_into_frontmatter(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(
            vault_path=vault,
            skip_notebooklm=False,
            notebook_map={"ops": "nb-ops"},
        )
        # Mock integrate_notebooklm to return a non-None source_id even
        # though run_intake passes note_path=None. The orchestrator
        # contract: when result.source_id is set, frontmatter.source_id
        # is updated via dataclasses.replace and rendered into final_markdown.
        # Codex review T "FRONTMATTER_MUTATION_TEST_IS_MOCKED_SHORTCUT"
        # 2026-04-30: this tests the orchestrator's replacement logic but
        # not the live Step 9 success path; the integration-shaped test
        # against real subprocess output lands with `confirm_and_write`,
        # which is the only place where note_path is non-None and Step 9
        # can actually succeed.
        fake_result = NotebookLMResult(
            source_id="src-ABC123",
            notebook_id="nb-ops",
            skipped=False,
            failed=False,
            queued=False,
            reason="added",
            source_count_warning=False,
        )
        with patch(
            "vault_intake.orchestrator.integrate_notebooklm",
            return_value=fake_result,
        ):
            result = run_intake(_OPS_INPUT, config)
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == "src-ABC123"
        assert "source_id: src-ABC123" in result.final_markdown

    def test_no_mutation_when_source_id_is_none(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, skip_notebooklm=True)
        result = run_intake(_OPS_INPUT, config)
        assert result.frontmatter is not None
        assert result.frontmatter.source_id == ""


# ---------------------------------------------------------------------------
# run_intake: emergent mode (NotImplementedError catch)
# ---------------------------------------------------------------------------


class TestRunIntakeEmergentMode:
    def test_emergent_mode_does_not_crash(self, tmp_path):
        vault = _make_emergent_vault(tmp_path, theme_folders=("infra",))
        config = _make_config(
            vault_path=vault,
            mode="emergent",
            domains=(),
        )
        # Step 3 (classify) raises NotImplementedError under emergent.
        # Orchestrator must catch and surface as a question rather than
        # propagating. Downstream steps that depend on classification
        # also short-circuit cleanly.
        result = run_intake(_OPS_INPUT, config)
        assert isinstance(result, IntakeRun)
        assert result.classification is None
        assert any("classify" in q.prompt for q in result.questions)

    def test_emergent_mode_step7_still_runs(self, tmp_path):
        vault = _make_emergent_vault(tmp_path)
        config = _make_config(vault_path=vault, mode="emergent", domains=())
        text = "Send the deck to Alice tomorrow. We need to ship the redesign by Friday."
        result = run_intake(text, config)
        # Step 7 is mode-agnostic; it should still run even when classify failed.
        assert result.next_actions.gate_fired is True

    def test_emergent_mode_full_cascade_in_questions(self, tmp_path):
        # Codex review R "EMERGENT_SKIPS_NOT_SURFACED" 2026-04-30: when emergent
        # classify raises, Steps 4-6 are also blocked under emergent v1; the
        # orchestrator surfaces the full cascade so the user understands the run
        # was incomplete, not just that classify alone was missing.
        vault = _make_emergent_vault(tmp_path)
        config = _make_config(vault_path=vault, mode="emergent", domains=())
        result = run_intake(_OPS_INPUT, config)
        questions_blob = "\n".join(q.prompt for q in result.questions)
        assert "classify" in questions_blob
        assert "categorize_para" in questions_blob
        assert "generate_frontmatter" in questions_blob
        assert "generate_wikilinks" in questions_blob


class TestRunIntakeFixedDomainsStepNotImplementedCatch:
    """Lock the NotImplementedError catch for Steps 4-6 in fixed_domains.

    Codex review T "EMERGENT_DEGRADATION_COVERAGE_TOO_SHALLOW" 2026-04-30:
    emergent-mode test coverage exercised only the Step 3 cascade. These
    tests simulate Steps 4, 5, 6 raising NotImplementedError in fixed_domains
    (a possible future state if emergent track partially ships) and verify
    the orchestrator catches each independently and continues the run.
    """

    def test_step4_notimplemented_caught(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        with patch(
            "vault_intake.orchestrator.categorize_para",
            side_effect=NotImplementedError("simulated"),
        ):
            result = run_intake(_OPS_INPUT, config)
        assert result.para is None
        # Step 5 cascades to skip because para is None in fixed_domains.
        assert result.frontmatter is None
        questions_blob = "\n".join(q.prompt for q in result.questions)
        assert "categorize_para" in questions_blob
        # Cascade non-pollution: only Step 4 raised NotImplementedError;
        # Steps 5/6 were dependency-blocked, not NotImplementedError-skipped,
        # so they must NOT appear in the not_implemented questions list (the
        # emergent cascade only fires under emergent mode). Codex confirmation
        # pass T2 PARTIAL 2026-04-30.
        assert "generate_frontmatter" not in questions_blob
        assert "generate_wikilinks" not in questions_blob

    def test_step5_notimplemented_caught(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        with patch(
            "vault_intake.orchestrator.generate_frontmatter",
            side_effect=NotImplementedError("simulated"),
        ):
            result = run_intake(_OPS_INPUT, config)
        assert result.frontmatter is None
        # Steps 8 and 9 depend on frontmatter; they skip cleanly.
        assert result.route is None
        assert result.notebooklm is None
        questions_blob = "\n".join(q.prompt for q in result.questions)
        assert "generate_frontmatter" in questions_blob

    def test_step6_notimplemented_caught(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        text = (
            "Working on launch-redesign ops project today. "
            "Send the deck to Alice tomorrow. "
            "Deploying ops infrastructure for launch-redesign work."
        )
        with patch(
            "vault_intake.orchestrator.generate_wikilinks",
            side_effect=NotImplementedError("simulated"),
        ):
            result = run_intake(text, config)
        # Steps 5, 7, 8, 9 still run successfully because they do not depend
        # on wikilinks output.
        assert result.frontmatter is not None
        assert result.wikilinks is None
        assert result.route is not None
        assert result.notebooklm is not None
        # Step 7 explicitly succeeds; codex confirmation pass T2 PARTIAL
        # 2026-04-30 asked for an explicit Step 7 assertion, not just a
        # NextActionsResult-not-None check.
        assert result.next_actions.gate_fired is True
        assert len(result.next_actions.proposals) > 0
        questions_blob = "\n".join(q.prompt for q in result.questions)
        assert "generate_wikilinks" in questions_blob


# ---------------------------------------------------------------------------
# IntakeRun.summary() per spec output contract (lines 228-243)
# ---------------------------------------------------------------------------


class TestIntakeRunSummary:
    def test_summary_includes_spec_contract_fields(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake(_OPS_INPUT, config)
        summary = result.summary()
        # Spec lines 228-243 fields (fixed_domains mode):
        assert "Processed:" in summary
        assert "Type:" in summary
        assert "Domain:" in summary
        assert "PARA:" in summary
        assert "Destination:" in summary
        assert "Wikilinks:" in summary
        assert "Next steps:" in summary
        assert "NotebookLM:" in summary
        assert "Captura original:" in summary

    def test_summary_emergent_uses_theme_label(self, tmp_path):
        # Even though classify NotImplemented in emergent mode, summary()
        # should still compose without raising. Theme/Domain label depends
        # on whether classification is set.
        vault = _make_emergent_vault(tmp_path)
        config = _make_config(vault_path=vault, mode="emergent", domains=())
        result = run_intake(_OPS_INPUT, config)
        summary = result.summary()
        # Domain/Theme line is only emitted when classification exists.
        assert "Domain:" not in summary
        assert "Theme:" not in summary

    def test_summary_queued_surface(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        with patch(
            "vault_intake.orchestrator.flush_nlm_queue",
            return_value=FlushResult(processed=0, still_queued=2, dropped=0),
        ):
            result = run_intake(_OPS_INPUT, config)
        summary = result.summary()
        assert "2 item(s) queued for NotebookLM" in summary
        assert "notebooklm login" in summary

    def test_summary_captura_preserved_when_changed(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault, refinement_enabled=True)
        text = _build_transcription()
        result = run_intake(text, config)
        summary = result.summary()
        assert "Captura original: preserved" in summary

    def test_summary_captura_not_needed_when_not_refined(self, tmp_path):
        vault = _make_fixed_domains_vault(tmp_path)
        config = _make_config(vault_path=vault)
        result = run_intake("# Doc\n\nShort.", config)
        summary = result.summary()
        assert "Captura original: not needed" in summary
