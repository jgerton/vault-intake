"""Orchestrator: wires Steps 0-9 into the spec's output contract.

Per build spec lines 228-243 the skill completes by presenting a
structured summary covering the source path, type, domain or theme,
PARA category (in fixed_domains/para mode only), destination, wikilink
count, next-step count, NotebookLM source ID or skip status, and
whether `## Captura original` was preserved.

Two entrypoints, locked 2026-04-30:

- `run_intake(input_text, config, ...) -> IntakeRun`: dry-run pass that
  produces all of the spec's summary content without touching the
  filesystem. `IntakeRun.written_path` is always None on this path.
- `confirm_and_write(intake_run, config, ...) -> IntakeRun`: post-
  confirmation entrypoint that performs the actual atomic file write,
  re-invokes Step 9 against the written path, mutates frontmatter on a
  non-None source_id, and re-writes atomically. Section-update routes
  (context+project) append to the existing project hub instead.

The CLI wrapper at `scripts/intake.py` is the explicit-confirmation
surface for spec safety rule 5; both entrypoints assume the caller has
already confirmed.

Pipeline ordering and gating, locked 2026-04-30:

1. Auto-drain the NotebookLM retry queue (best-effort; surfaces in
   `queued_nlm_count`).
2. Step 1: detect content type.
3. Step 2: refine, gated on `config.refinement_enabled` AND
   `detection.refinement_applicable`.
4. Step 3: classify, wraps NotImplementedError so emergent-mode runs
   degrade gracefully.
5. Step 4: PARA, skipped in emergent mode; wraps NotImplementedError.
6. Step 5: generate frontmatter, wraps NotImplementedError; needs a
   non-None ParaResult in fixed_domains so a skipped Step 4 also skips
   Step 5.
7. Step 6: generate wikilinks, wraps NotImplementedError; needs
   classification plus para in fixed_domains.
8. Step 7: extract next-actions, mode-agnostic and content-driven; runs
   regardless of upstream skips.
9. Step 8: route, needs classification plus frontmatter; needs para in
   fixed_domains.
10. Step 9: NotebookLM, always called with `note_path=None` in dry-run
    so it returns a skipped result; the result still flows through to
    `IntakeRun.notebooklm` for completeness.

The orchestrator owns ALL Frontmatter mutations. When Step 9 returns a
non-None `source_id`, `frontmatter` is updated via `dataclasses.replace`
before final markdown assembly. Library functions never mutate their
inputs.
"""
from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .classify import ClassificationResult, classify
from .config import Config
from .detect import DetectionResult, detect_content_type
from .frontmatter import Frontmatter, SourceType, generate_frontmatter
from .next_actions import NextActionsResult, extract_next_actions
from .notebooklm import (
    NotebookLMResult,
    flush_nlm_queue,
    integrate_notebooklm,
)
from .para import ParaResult
from .para import categorize_para
from .refine import RefinedContent, refine
from .route import RouteResult, route
from .wikilinks import WikilinkResult, generate_wikilinks


# ---------------------------------------------------------------------------
# IntakeQuestion structured-question shape
# ---------------------------------------------------------------------------


class QuestionKind(StrEnum):
    """Routing tag for `IntakeQuestion`.

    The CLI wrapper dispatches each question's answer to the right
    `IntakeRun` field by inspecting `kind` rather than parsing the
    free-form prompt text. `NOT_IMPLEMENTED` questions are
    informational only and never accept an answer.
    """

    DETECTION_TYPE = "detection.type"
    CLASSIFICATION = "classification.primary"
    PARA = "para.category"
    ROUTE_ARCHIVE = "route.archive"
    FRONTMATTER_TITLE = "frontmatter.title"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True)
class IntakeQuestion:
    """A single confirmation prompt collected by `collect_questions`.

    `prompt` is the user-facing text. `suggested` is the current value
    the user can accept (None for `NOT_IMPLEMENTED`, where there is no
    choice to make). `step` is set only when `kind == NOT_IMPLEMENTED`
    to identify which pipeline step was skipped. `content_snippet` is
    set only for CLASSIFICATION questions; it carries a short excerpt
    of the note body so the user can confirm the domain with context.
    """

    kind: QuestionKind
    prompt: str
    suggested: str | None
    step: str | None = None
    content_snippet: str | None = None


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeRun:
    """Structured result of a single `run_intake` invocation.

    Library-API contract: every field except `next_actions`, `body`,
    `final_markdown`, `written_path`, `queued_nlm_count`, and
    `questions` may be None when an upstream step raised
    NotImplementedError or when the orchestrator decided to skip the
    step under the active mode. `next_actions` always exists because
    Step 7 is mode-agnostic and never raises. `body` carries the post-
    refinement text (or the raw input when Step 2 is skipped) so
    `confirm_and_write` can build section-update content without
    parsing it back out of `final_markdown` (which would mis-split
    when the body itself happens to contain a `## Possíveis próximos
    passos` or `## Captura original` heading).
    """

    detection: DetectionResult
    refinement: RefinedContent | None
    classification: ClassificationResult | None
    para: ParaResult | None
    frontmatter: Frontmatter | None
    wikilinks: WikilinkResult | None
    next_actions: NextActionsResult
    route: RouteResult | None
    notebooklm: NotebookLMResult | None
    body: str
    final_markdown: str
    written_path: Path | None
    queued_nlm_count: int
    questions: tuple[IntakeQuestion, ...]

    def summary(self) -> str:
        """Render the spec output contract per build spec lines 228-243.

        Field set varies by mode: `Domain` only in fixed_domains;
        `Theme` only in emergent (when classification is set); `PARA`
        only when `routing_mode: para` produced a ParaResult.
        """
        lines: list[str] = []

        # Processed
        if self.written_path is not None:
            lines.append(f"Processed: {self.written_path}")
        elif self.route is not None:
            lines.append(f"Processed: {self.route.destination} (dry-run)")
        else:
            lines.append("Processed: (dry-run, no destination)")

        # Type
        if self.frontmatter is not None:
            lines.append(f"Type: {self.frontmatter.type}")
        else:
            lines.append(f"Type: {self.detection.type}")

        # Domain or Theme (only when classification exists)
        if self.classification is not None:
            if self.classification.mode == "fixed_domains":
                tags = self.classification.secondary
                if tags:
                    lines.append(
                        f"Domain: {self.classification.primary} (+ {', '.join(tags)})"
                    )
                else:
                    lines.append(f"Domain: {self.classification.primary}")
            else:
                lines.append(f"Theme: {self.classification.primary}")

        # PARA (only when para set)
        if self.para is not None:
            lines.append(f"PARA: {self.para.category}")

        # Destination + routing rationale
        if self.route is not None:
            lines.append(f"Destination: {self.route.destination}")
            lines.append(f"Route: {self.route.reason}")

        # Wikilinks
        if self.wikilinks is not None:
            count = len(self.wikilinks.proposals)
            if count == 0:
                lines.append("Wikilinks: 0")
            else:
                preview = ", ".join(p.target for p in self.wikilinks.proposals[:5])
                lines.append(f"Wikilinks: {count} ({preview})")

        # Next steps
        lines.append(f"Next steps: {len(self.next_actions.proposals)}")

        # NotebookLM
        nlm_text = _format_notebooklm(self.notebooklm)
        lines.append(f"NotebookLM: {nlm_text}")

        # Captura original
        if self.refinement is not None and self.refinement.changed:
            lines.append("Captura original: preserved")
        else:
            lines.append("Captura original: not needed")

        # Queue surface (when nonzero)
        if self.queued_nlm_count > 0:
            lines.append("")
            lines.append(
                f"{self.queued_nlm_count} item(s) queued for NotebookLM (auth expired)."
            )
            lines.append(
                "Run `notebooklm login` then `vault-intake flush-nlm` to drain."
            )

        # Confirmations
        if self.questions:
            lines.append("")
            lines.append("Confirmations needed:")
            for question in self.questions:
                lines.append(f"- {question.prompt}")

        return "\n".join(lines)


def _format_notebooklm(result: NotebookLMResult | None) -> str:
    if result is None:
        return "skipped"
    if result.source_id:
        return result.source_id
    if result.queued:
        return "queued (auth expired)"
    return "skipped"


# ---------------------------------------------------------------------------
# Helpers (assemble_final_markdown, collect_questions)
# ---------------------------------------------------------------------------


def assemble_final_markdown(
    *,
    body: str,
    frontmatter: Frontmatter,
    refinement: RefinedContent | None,
    next_actions: NextActionsResult,
) -> str:
    """Assemble the final markdown per kickoff item 4.

    Layout:

    ```
    ---
    {frontmatter.to_yaml()}
    ---

    {body}

    {next_actions.to_markdown() if gate_fired}

    ## Captura original           <- only when refinement.changed
    {refinement.original}
    ```

    Wikilink proposals are NOT auto-appended per safety rule 5; the
    orchestrator surfaces them in `IntakeRun.wikilinks` for the user to
    confirm at session-end.
    """
    parts: list[str] = [f"---\n{frontmatter.to_yaml()}---", "", body.strip()]
    next_md = next_actions.to_markdown()
    if next_md:
        parts.extend(["", next_md])
    if refinement is not None and refinement.changed:
        parts.extend(["", "## Captura original", "", refinement.original.strip()])
    return "\n".join(parts) + "\n"


def _extract_content_snippet(body: str, *, max_chars: int = 200) -> str:
    """Return a short excerpt from body for classification context.

    Takes up to 3 sentences or max_chars characters, whichever is shorter.
    Strips YAML frontmatter fences and markdown headings from the start.
    """
    # Strip leading frontmatter block (---...---) if present.
    text = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.DOTALL)
    # Strip leading markdown headings.
    text = re.sub(r"^#{1,6}[^\n]*\n", "", text.lstrip(), flags=re.MULTILINE)
    text = text.strip()
    if not text:
        return ""
    # Split on sentence-ending punctuation followed by whitespace or end.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    snippet = ""
    for sentence in sentences[:3]:
        candidate = (snippet + " " + sentence).strip() if snippet else sentence
        if len(candidate) > max_chars:
            break
        snippet = candidate
    if not snippet:
        snippet = text[:max_chars]
    return snippet.strip()


def collect_questions(
    *,
    detection: DetectionResult,
    classification: ClassificationResult | None,
    para: ParaResult | None,
    route: RouteResult | None,
    frontmatter: Frontmatter | None,
    not_implemented: tuple[str, ...] = (),
    body: str = "",
) -> tuple[IntakeQuestion, ...]:
    """Collect uncertainty signals into a tuple of `IntakeQuestion`.

    Per kickoff item 7: detection / classification / PARA uncertainty,
    archive flagging, and the always-confirm title heuristic. Plus one
    informational `NOT_IMPLEMENTED` entry per pipeline step that raised
    NotImplementedError so the user understands what was not produced.

    `kind` lets the CLI wrapper route each answer to the right
    `IntakeRun` field without parsing prompt text.
    """
    questions: list[IntakeQuestion] = []
    if detection.uncertain:
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.DETECTION_TYPE,
                prompt=f"I detected this as `{detection.type}`; correct?",
                suggested=detection.type,
            )
        )
    if classification is not None and classification.uncertain:
        snippet = _extract_content_snippet(body) if body else None
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.CLASSIFICATION,
                prompt=f"I classified as `{classification.primary}`; correct?",
                suggested=classification.primary,
                content_snippet=snippet or None,
            )
        )
    if para is not None and para.uncertain:
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.PARA,
                prompt=f"I categorized as `{para.category}`; correct?",
                suggested=para.category,
            )
        )
    if route is not None and route.archive_flagged:
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.ROUTE_ARCHIVE,
                prompt=(
                    f"PARA=archive flagged; route to `{route.destination}` "
                    "or move to `archive/`?"
                ),
                suggested=str(route.destination),
            )
        )
    if frontmatter is not None and frontmatter.title:
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.FRONTMATTER_TITLE,
                prompt=(
                    f"Title heuristic produced `{frontmatter.title}`; "
                    "confirm or override?"
                ),
                suggested=frontmatter.title,
            )
        )
    for step_name in not_implemented:
        questions.append(
            IntakeQuestion(
                kind=QuestionKind.NOT_IMPLEMENTED,
                prompt=(
                    f"`{step_name}` is not yet implemented in this mode; "
                    "this part of the run was skipped."
                ),
                suggested=None,
                step=step_name,
            )
        )
    return tuple(questions)


# ---------------------------------------------------------------------------
# run_intake (Phase 2 entrypoint)
# ---------------------------------------------------------------------------


def run_intake(
    input_text: str,
    config: Config,
    *,
    source_type: SourceType = "paste",
    source_uri: str = "",
    captured_at: str | None = None,
    nlm_command: str = "notebooklm",
) -> IntakeRun:
    """Wire Steps 0-9 into a single dry-run pass over `input_text`.

    Returns a frozen `IntakeRun`. Never raises on user input under v1
    contract; library functions that raise NotImplementedError under
    emergent mode are caught and surfaced as `questions` rather than
    propagating. Subprocess errors from the auto-drain prelude are
    silently swallowed; the run continues.
    """
    # Auto-drain: best-effort, surfaces still_queued in queued_nlm_count.
    try:
        flush = flush_nlm_queue(config, nlm_command=nlm_command)
        still_queued_initial = flush.still_queued
    except Exception:  # noqa: BLE001 - never-block contract
        still_queued_initial = 0

    # Step 1: detect.
    detection = detect_content_type(input_text)

    # Step 2: refine (gated).
    refinement: RefinedContent | None = None
    if config.refinement_enabled and detection.refinement_applicable:
        refinement = refine(input_text)

    body = refinement.refined if refinement is not None else input_text

    # Step 3: classify.
    classification: ClassificationResult | None = None
    not_implemented: list[str] = []
    try:
        classification = classify(body, config)
    except NotImplementedError:
        not_implemented.append("classify")

    # Step 4: PARA (fixed_domains only).
    para: ParaResult | None = None
    if classification is not None and config.mode == "fixed_domains":
        try:
            para = categorize_para(body, detection, classification, config)
        except NotImplementedError:
            not_implemented.append("categorize_para")

    # Step 5: frontmatter.
    frontmatter: Frontmatter | None = None
    if classification is not None and (
        config.mode == "emergent" or para is not None
    ):
        try:
            frontmatter = generate_frontmatter(
                text=body,
                detection=detection,
                refinement=refinement,
                classification=classification,
                # generate_frontmatter requires a ParaResult under fixed_domains;
                # in emergent mode it raises NotImplementedError before reading
                # the argument, so passing the fixed_domains para (or None) is
                # safe either way.
                para=para,  # type: ignore[arg-type]
                config=config,
                source_type=source_type,
                source_uri=source_uri,
                captured_at=captured_at,
            )
        except NotImplementedError:
            not_implemented.append("generate_frontmatter")

    # Step 6: wikilinks.
    wikilinks: WikilinkResult | None = None
    if (
        classification is not None
        and config.mode == "fixed_domains"
        and para is not None
    ):
        try:
            wikilinks = generate_wikilinks(
                text=body,
                classification=classification,
                para=para,
                config=config,
            )
        except NotImplementedError:
            not_implemented.append("generate_wikilinks")

    # Step 7: next-actions (mode-agnostic, never raises).
    next_actions = extract_next_actions(text=body, config=config)

    # Step 8: route.
    route_result: RouteResult | None = None
    if classification is not None and frontmatter is not None:
        if config.mode == "fixed_domains" and para is not None:
            route_result = route(
                detection=detection,
                classification=classification,
                para=para,
                frontmatter=frontmatter,
                config=config,
            )
        elif config.mode == "emergent":
            route_result = route(
                detection=detection,
                classification=classification,
                para=None,
                frontmatter=frontmatter,
                config=config,
            )

    # Step 9: NotebookLM (dry-run; note_path=None always in this entrypoint).
    notebooklm_result: NotebookLMResult | None = None
    if classification is not None and frontmatter is not None:
        notebooklm_result = integrate_notebooklm(
            classification=classification,
            frontmatter=frontmatter,
            config=config,
            note_path=None,
            nlm_command=nlm_command,
        )

    # Frontmatter mutation: thread Step 9's source_id back through.
    # Dry-run path returns source_id=None so this is a no-op in v1
    # `run_intake`; the contract still exists for tests and for the
    # eventual `confirm_and_write` integration.
    if (
        frontmatter is not None
        and notebooklm_result is not None
        and notebooklm_result.source_id
    ):
        frontmatter = dataclasses.replace(
            frontmatter, source_id=notebooklm_result.source_id
        )

    # Final markdown assembly.
    final_markdown = ""
    if frontmatter is not None:
        final_markdown = assemble_final_markdown(
            body=body,
            frontmatter=frontmatter,
            refinement=refinement,
            next_actions=next_actions,
        )

    # Emergent-mode cascade: when Step 3 raised NotImplementedError, Steps 4,
    # 5, and 6 are also blocked under emergent v1 (all four raise in their
    # respective library modules). Surface the full cascade so the user
    # understands the run was incomplete, rather than only flagging the first
    # step that raised. Codex review R "EMERGENT_SKIPS_NOT_SURFACED" 2026-04-30.
    if config.mode == "emergent" and "classify" in not_implemented:
        for downstream in (
            "categorize_para",
            "generate_frontmatter",
            "generate_wikilinks",
        ):
            if downstream not in not_implemented:
                not_implemented.append(downstream)

    questions = collect_questions(
        detection=detection,
        classification=classification,
        para=para,
        route=route_result,
        frontmatter=frontmatter,
        not_implemented=tuple(not_implemented),
        body=body,
    )

    queued_this_run = (
        1 if notebooklm_result is not None and notebooklm_result.queued else 0
    )

    return IntakeRun(
        detection=detection,
        refinement=refinement,
        classification=classification,
        para=para,
        frontmatter=frontmatter,
        wikilinks=wikilinks,
        next_actions=next_actions,
        route=route_result,
        notebooklm=notebooklm_result,
        body=body,
        final_markdown=final_markdown,
        written_path=None,
        queued_nlm_count=still_queued_initial + queued_this_run,
        questions=questions,
    )


# ---------------------------------------------------------------------------
# confirm_and_write (Phase 2 entrypoint, post-confirmation file write)
# ---------------------------------------------------------------------------


def confirm_and_write(
    intake_run: IntakeRun,
    config: Config,
    *,
    nlm_command: str = "notebooklm",
    overwrite: bool = False,
) -> IntakeRun:
    """Write the confirmed intake run to disk, then re-invoke Step 9 live.

    Mechanical: never prompts. The CLI wrapper resolves any uncertainty
    (title overrides, collision choice) before calling this function.

    Two paths, branching on `intake_run.route.is_section_update`:

    - Regular write: `{frontmatter.title}.md` placed at
      `route.destination` (a folder). Atomic via temp file plus
      `os.replace`. Raises `FileExistsError` when the target exists
      unless `overwrite=True`. Re-invokes `integrate_notebooklm` with
      the written `note_path`. On non-None `source_id`, mutates
      frontmatter via `dataclasses.replace`, re-renders final markdown,
      and re-writes the file atomically.

    - Section update: `route.destination` IS the file path (existing
      project hub). Appends a `## {title}` section plus optional
      `## Captura original` block. Raises `FileNotFoundError` when the
      destination does not exist. Live Step 9 is skipped (the project
      hub may already be a NotebookLM source; re-adding would create
      duplicates).

    Defense in depth (spec safety rule 6): the destination must be
    inside `config.vault_path`. Raises `ValueError` otherwise even
    though `route()` already constrains.

    `queued_nlm_count` carries forward from the input IntakeRun and
    increments by one when the live Step 9 result has `queued=True`.
    """
    if intake_run.route is None:
        raise ValueError(
            "confirm_and_write requires intake_run.route to be set; got None"
        )
    if intake_run.frontmatter is None:
        raise ValueError(
            "confirm_and_write requires intake_run.frontmatter to be set; got None"
        )

    _check_destination_inside_vault(
        intake_run.route.destination, config.vault_path
    )

    if intake_run.route.is_section_update:
        return _confirm_and_write_section_update(intake_run)
    return _confirm_and_write_regular(
        intake_run, config, nlm_command=nlm_command, overwrite=overwrite
    )


def _check_destination_inside_vault(destination: Path, vault_path: Path) -> None:
    """Raise ValueError unless `destination` is inside `vault_path`.

    Resolves both paths so symlink trickery cannot route writes outside
    the vault. The vault folder itself is treated as inside (a no-op
    `relative_to(self)` returns `Path('.')`).
    """
    resolved_dest = destination.resolve()
    resolved_vault = vault_path.resolve()
    if not resolved_dest.is_relative_to(resolved_vault):
        raise ValueError(
            f"destination {destination} is not inside vault_path {vault_path}"
        )


def _atomic_write(target: Path, content: str) -> None:
    """Write `content` to `target` atomically via temp file plus os.replace."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _confirm_and_write_regular(
    intake_run: IntakeRun,
    config: Config,
    *,
    nlm_command: str,
    overwrite: bool,
) -> IntakeRun:
    assert intake_run.route is not None  # narrowed by caller
    assert intake_run.frontmatter is not None  # narrowed by caller
    assert intake_run.classification is not None, (
        "regular write requires a classification; orchestrator skips routing without one"
    )

    target_dir = intake_run.route.destination
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{intake_run.frontmatter.title}.md"

    if target_file.exists() and not overwrite:
        raise FileExistsError(f"target file already exists: {target_file}")

    _atomic_write(target_file, intake_run.final_markdown)

    # Re-invoke Step 9 against the written path.
    live_result = integrate_notebooklm(
        classification=intake_run.classification,
        frontmatter=intake_run.frontmatter,
        config=config,
        note_path=target_file,
        nlm_command=nlm_command,
    )

    new_frontmatter = intake_run.frontmatter
    new_final_markdown = intake_run.final_markdown
    if live_result.source_id:
        new_frontmatter = dataclasses.replace(
            intake_run.frontmatter, source_id=live_result.source_id
        )
        new_final_markdown = _replace_frontmatter_block(
            intake_run.final_markdown, new_frontmatter
        )
        _atomic_write(target_file, new_final_markdown)

    queued_increment = 1 if live_result.queued else 0

    return dataclasses.replace(
        intake_run,
        frontmatter=new_frontmatter,
        final_markdown=new_final_markdown,
        notebooklm=live_result,
        written_path=target_file,
        queued_nlm_count=intake_run.queued_nlm_count + queued_increment,
    )


def _confirm_and_write_section_update(intake_run: IntakeRun) -> IntakeRun:
    assert intake_run.route is not None
    assert intake_run.frontmatter is not None

    target = intake_run.route.destination
    if not target.exists():
        raise FileNotFoundError(
            f"section-update target does not exist: {target}"
        )

    section_md = _build_section_markdown(intake_run)
    existing = target.read_text(encoding="utf-8")
    # Collapse trailing newlines only (not other whitespace) so the
    # appended section sits two newlines below the existing content
    # while preserving any trailing whitespace on the last content
    # line. Codex review N "section-update whitespace normalization"
    # 2026-04-30.
    new_content = existing.rstrip("\n") + "\n\n" + section_md
    if not new_content.endswith("\n"):
        new_content += "\n"
    _atomic_write(target, new_content)

    skipped_result = NotebookLMResult(
        source_id=None,
        notebook_id=None,
        skipped=True,
        failed=False,
        queued=False,
        reason="section update: existing project hub not re-added to NotebookLM",
        source_count_warning=False,
    )

    return dataclasses.replace(
        intake_run,
        notebooklm=skipped_result,
        written_path=target,
    )


def _replace_frontmatter_block(markdown: str, frontmatter: Frontmatter) -> str:
    """Swap the YAML block in `markdown` for the rendered `frontmatter`.

    Relies on the fact that `assemble_final_markdown` always emits
    `---\\n{yaml}---\\n` as the leading block. The rest of the document
    (body plus optional trailing sections) is preserved verbatim.
    """
    new_yaml = frontmatter.to_yaml()
    return re.sub(
        r"\A---\n.*?\n---\n",
        f"---\n{new_yaml}---\n",
        markdown,
        count=1,
        flags=re.DOTALL,
    )


def _build_section_markdown(intake_run: IntakeRun) -> str:
    """Render the body of a section appended to a project hub.

    Layout: `## {title}` heading, then the post-refinement body (or the
    raw body when Step 2 was skipped) carried verbatim from
    `intake_run.body`, then an optional `## Captura original` block
    when refinement.changed. Next-actions are intentionally omitted
    from the appended section (they remain accessible via
    `IntakeRun.next_actions` for the CLI).
    """
    assert intake_run.frontmatter is not None
    parts = [f"## {intake_run.frontmatter.title}", "", intake_run.body.strip()]
    if intake_run.refinement is not None and intake_run.refinement.changed:
        parts.extend(
            ["", "## Captura original", "", intake_run.refinement.original.strip()]
        )
    return "\n".join(parts)


__all__ = [
    "IntakeQuestion",
    "IntakeRun",
    "QuestionKind",
    "assemble_final_markdown",
    "collect_questions",
    "confirm_and_write",
    "run_intake",
]
