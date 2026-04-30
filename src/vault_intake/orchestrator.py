"""Orchestrator: wires Steps 0-9 into the spec's output contract.

Per build spec lines 228-243 the skill completes by presenting a
structured summary covering the source path, type, domain or theme,
PARA category (in fixed_domains/para mode only), destination, wikilink
count, next-step count, NotebookLM source ID or skip status, and
whether `## Captura original` was preserved. The orchestrator produces
all of that via a single `run_intake(input_text, config, ...)` call
that returns a frozen `IntakeRun`.

This module is dry-run only in v1: `IntakeRun.written_path` is always
None. A separate `confirm_and_write` function (next commit) handles the
actual file write, the live Step 9 invocation against the written path,
and the corresponding `frontmatter.source_id` update.

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
from dataclasses import dataclass
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
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeRun:
    """Structured result of a single `run_intake` invocation.

    Library-API contract: every field except `next_actions`,
    `final_markdown`, `written_path`, `queued_nlm_count`, and
    `questions` may be None when an upstream step raised
    NotImplementedError or when the orchestrator decided to skip the
    step under the active mode. `next_actions` always exists because
    Step 7 is mode-agnostic and never raises.
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
    final_markdown: str
    written_path: Path | None
    queued_nlm_count: int
    questions: tuple[str, ...]

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

        # Destination
        if self.route is not None:
            lines.append(f"Destination: {self.route.destination}")

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
                lines.append(f"- {question}")

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


def collect_questions(
    *,
    detection: DetectionResult,
    classification: ClassificationResult | None,
    para: ParaResult | None,
    route: RouteResult | None,
    frontmatter: Frontmatter | None,
    not_implemented: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Collect uncertainty signals into a tuple of confirmation questions.

    Per kickoff item 7: detection / classification / PARA uncertainty,
    archive flagging, and the always-confirm title heuristic. Plus a
    line per NotImplementedError-skipped step so the user understands
    what was not produced.
    """
    questions: list[str] = []
    if detection.uncertain:
        questions.append(f"I detected this as `{detection.type}`; correct?")
    if classification is not None and classification.uncertain:
        questions.append(f"I classified as `{classification.primary}`; correct?")
    if para is not None and para.uncertain:
        questions.append(f"I categorized as `{para.category}`; correct?")
    if route is not None and route.archive_flagged:
        questions.append(
            f"PARA=archive flagged; route to `{route.destination}` or move to `archive/`?"
        )
    if frontmatter is not None and frontmatter.title:
        questions.append(
            f"Title heuristic produced `{frontmatter.title}`; confirm or override?"
        )
    for step_name in not_implemented:
        questions.append(
            f"`{step_name}` is not yet implemented in this mode; "
            "this part of the run was skipped."
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

    questions = collect_questions(
        detection=detection,
        classification=classification,
        para=para,
        route=route_result,
        frontmatter=frontmatter,
        not_implemented=tuple(not_implemented),
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
        final_markdown=final_markdown,
        written_path=None,
        queued_nlm_count=still_queued_initial + queued_this_run,
        questions=questions,
    )


__all__ = [
    "IntakeRun",
    "assemble_final_markdown",
    "collect_questions",
    "run_intake",
]
