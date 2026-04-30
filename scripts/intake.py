"""CLI wrapper for the vault-intake skill (M1 dogfood loop).

Usage:
    uv run scripts/intake.py [options]

Reads input from stdin or `--input PATH`, runs the orchestrator's
dry-run pass, prompts the user (unless `--yes`), then commits via
`confirm_and_write`. Spec safety rule 5 (never write without
confirmation) is satisfied here: `--yes` is the explicit pre-approval
opt-in.

Exit codes:
    0  successful write or successful --dry-run
    1  user aborted (write confirmation, collision prompt, or EOF /
       KeyboardInterrupt during a prompt)
    2  config error (missing vault, malformed CLAUDE.md, missing
       CLAUDE.md, TTY-stdin refusal)
    3  pipeline error (orchestrator raised; should not happen by
       contract but guarded)
    4  file write error (FileExistsError without --overwrite,
       FileNotFoundError on section-update missing hub, ValueError on
       out-of-vault destination, OSError)
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path
from typing import Iterable, get_args

from vault_intake.config import Config, ConfigError, resolve_config
from vault_intake.frontmatter import SourceType
from vault_intake.orchestrator import (
    IntakeQuestion,
    IntakeRun,
    QuestionKind,
    assemble_final_markdown,
    confirm_and_write,
    run_intake,
)


EXIT_SUCCESS = 0
EXIT_USER_ABORTED = 1
EXIT_CONFIG_ERROR = 2
EXIT_PIPELINE_ERROR = 3
EXIT_WRITE_ERROR = 4

_PREVIEW_LINES = 30


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intake.py",
        description="Vault-intake CLI: run the pipeline and write a confirmed note.",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("VAULT_INTAKE_VAULT_PATH", ""),
        help="Vault root path. Falls back to env VAULT_INTAKE_VAULT_PATH.",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Read input text from this file instead of stdin.",
    )
    parser.add_argument(
        "--source-type",
        default="paste",
        choices=list(get_args(SourceType)),
        help="Frontmatter source_type (default: paste).",
    )
    parser.add_argument(
        "--source-uri",
        default="",
        help="Frontmatter source_uri (default: empty).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Override frontmatter title; skips the title prompt.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept all suggestions; render no prompts.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If the destination file exists, overwrite it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop after the orchestrator's dry-run; do not write.",
    )
    parser.add_argument(
        "--nlm-command",
        default="notebooklm",
        help="Override the notebooklm CLI command (default: notebooklm).",
    )
    parser.add_argument(
        "--skip-notebooklm",
        action="store_true",
        help="Force-disable Step 9 NotebookLM integration for this run.",
    )
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    if args.title is not None:
        # Empty/whitespace `--title` would silently produce a blank
        # frontmatter title and a `.md` filename. Codex review R4
        # 2026-04-30: validate before any pipeline work runs.
        cleaned = args.title.strip()
        if not cleaned:
            print(
                "error: --title must be a non-empty value",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        args.title = cleaned

    config = _resolve_config_from_args(args)
    if config is None:
        return EXIT_CONFIG_ERROR

    input_text = _read_input(args)
    if input_text is None:
        return EXIT_CONFIG_ERROR

    try:
        run = run_intake(
            input_text,
            config,
            source_type=args.source_type,
            source_uri=args.source_uri,
            nlm_command=args.nlm_command,
        )
    except Exception as exc:  # noqa: BLE001 - orchestrator should not raise; guarded
        print(f"pipeline error: {exc}", file=sys.stderr)
        return EXIT_PIPELINE_ERROR

    print(run.summary())
    print()
    _print_preview(run.final_markdown)

    # `--title` override: applies before any prompts so the prompt loop
    # skips the FRONTMATTER_TITLE question. Mirrors how `confirm_and_write`
    # treats frontmatter mutations: orchestrator-only, dataclasses.replace,
    # final markdown re-assembled.
    if args.title is not None and run.frontmatter is not None:
        run = _override_title(run, args.title)

    skip_kinds: set[QuestionKind] = set()
    if args.title is not None:
        skip_kinds.add(QuestionKind.FRONTMATTER_TITLE)

    if not args.yes:
        try:
            run = _prompt_for_questions(run, skip_kinds=skip_kinds)
        except (KeyboardInterrupt, EOFError):
            print("\naborted", file=sys.stderr)
            return EXIT_USER_ABORTED

    if args.dry_run:
        print()
        print("dry-run; not writing")
        return EXIT_SUCCESS

    if not args.yes:
        try:
            confirmed = _prompt_write_confirmation(run)
        except (KeyboardInterrupt, EOFError):
            print("\naborted", file=sys.stderr)
            return EXIT_USER_ABORTED
        if not confirmed:
            print("aborted", file=sys.stderr)
            return EXIT_USER_ABORTED

    return _attempt_write(run, config, args)


# ---------------------------------------------------------------------------
# Config resolution and input reading
# ---------------------------------------------------------------------------


def _resolve_config_from_args(args: argparse.Namespace) -> Config | None:
    vault_str = (args.vault or "").strip()
    if not vault_str:
        print(
            "error: --vault PATH required (or set VAULT_INTAKE_VAULT_PATH)",
            file=sys.stderr,
        )
        return None

    vault = Path(vault_str)
    claude_md = vault / "CLAUDE.md"
    if not claude_md.exists():
        print(f"error: vault CLAUDE.md not found at {claude_md}", file=sys.stderr)
        return None

    try:
        config = resolve_config(claude_md)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return None
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    if args.skip_notebooklm:
        config = dataclasses.replace(config, skip_notebooklm=True)
    return config


def _read_input(args: argparse.Namespace) -> str | None:
    if args.input:
        try:
            return Path(args.input).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None
    if sys.stdin.isatty():
        print(
            "error: no --input given and stdin is a TTY "
            "(refusing to block on terminal input)",
            file=sys.stderr,
        )
        return None
    return sys.stdin.read()


# ---------------------------------------------------------------------------
# Preview and prompts
# ---------------------------------------------------------------------------


def _print_preview(markdown: str, *, limit: int = _PREVIEW_LINES) -> None:
    lines = markdown.splitlines()
    if len(lines) <= limit:
        for line in lines:
            print(line)
    else:
        for line in lines[:limit]:
            print(line)
        print(f"... [{len(lines) - limit} more lines]")


def _prompt_for_questions(
    run: IntakeRun,
    *,
    skip_kinds: Iterable[QuestionKind] = (),
) -> IntakeRun:
    """Render each `IntakeQuestion` with its suggested answer pre-filled.

    `NOT_IMPLEMENTED` questions are informational only; they print but
    do not solicit input. Other kinds either accept the suggested answer
    on Enter or take a free-text override. v1 only routes the
    `FRONTMATTER_TITLE` answer back into the run (other kinds do not yet
    re-run the upstream pipeline; future work).
    """
    skip_set = set(skip_kinds)
    new_run = run
    for question in run.questions:
        if question.kind in skip_set:
            continue
        print()
        print(question.prompt)
        if question.kind == QuestionKind.NOT_IMPLEMENTED:
            # Informational only; no answer to collect.
            continue
        suggested = question.suggested or ""
        if suggested:
            print(f"  suggested: {suggested}")
        answer = input("  > ").strip()
        if not answer:
            answer = suggested
        new_run = _apply_answer(new_run, question.kind, answer)
    return new_run


def _apply_answer(run: IntakeRun, kind: QuestionKind, answer: str) -> IntakeRun:
    if kind == QuestionKind.FRONTMATTER_TITLE and run.frontmatter is not None:
        return _override_title(run, answer)
    # Other kinds (DETECTION_TYPE, CLASSIFICATION, PARA, ROUTE_ARCHIVE):
    # in v1 the wrapper does not re-run the upstream pipeline mid-prompt;
    # the user's confirmation is recorded implicitly by accepting the
    # current run. Future work: re-run downstream steps with overrides.
    return run


def _override_title(run: IntakeRun, title: str) -> IntakeRun:
    assert run.frontmatter is not None
    new_fm = dataclasses.replace(run.frontmatter, title=title)
    new_md = assemble_final_markdown(
        body=run.body,
        frontmatter=new_fm,
        refinement=run.refinement,
        next_actions=run.next_actions,
    )
    return dataclasses.replace(run, frontmatter=new_fm, final_markdown=new_md)


def _prompt_write_confirmation(run: IntakeRun) -> bool:
    if run.frontmatter is None or run.route is None:
        print("error: pipeline did not produce a writable run", file=sys.stderr)
        return False
    if run.route.is_section_update:
        target = run.route.destination
    else:
        target = run.route.destination / f"{run.frontmatter.title}.md"
    print()
    print(f"Write to {target}? [Y/n/a]")
    answer = input("  > ").strip().lower()
    return answer in ("", "y", "yes")


def _prompt_collision_choice() -> str:
    print()
    print("File already exists. [O]verwrite, [R]ename (auto-suffix -2), [A]bort?")
    answer = input("  > ").strip().lower()
    if answer in ("o", "overwrite"):
        return "overwrite"
    if answer in ("r", "rename"):
        return "rename"
    return "abort"


def _auto_rename(run: IntakeRun) -> IntakeRun:
    """Find next available `{title}-N.md` and update frontmatter.title."""
    assert run.frontmatter is not None and run.route is not None
    base = run.frontmatter.title
    target_dir = run.route.destination
    n = 2
    while (target_dir / f"{base}-{n}.md").exists():
        n += 1
    return _override_title(run, f"{base}-{n}")


# ---------------------------------------------------------------------------
# Write attempt and exit-code mapping
# ---------------------------------------------------------------------------


def _attempt_write(run: IntakeRun, config: Config, args: argparse.Namespace) -> int:
    try:
        result = confirm_and_write(
            run,
            config,
            nlm_command=args.nlm_command,
            overwrite=args.overwrite,
        )
    except FileExistsError as exc:
        if args.yes:
            print(
                f"write error: {exc}; pass --overwrite to replace",
                file=sys.stderr,
            )
            return EXIT_WRITE_ERROR
        try:
            choice = _prompt_collision_choice()
        except (KeyboardInterrupt, EOFError):
            print("\naborted", file=sys.stderr)
            return EXIT_USER_ABORTED
        if choice == "abort":
            print("aborted", file=sys.stderr)
            return EXIT_USER_ABORTED
        try:
            if choice == "overwrite":
                result = confirm_and_write(
                    run,
                    config,
                    nlm_command=args.nlm_command,
                    overwrite=True,
                )
            else:
                run = _auto_rename(run)
                result = confirm_and_write(
                    run,
                    config,
                    nlm_command=args.nlm_command,
                    overwrite=False,
                )
        except FileExistsError as exc:
            # TOCTOU: another writer claimed the renamed slot between
            # `_auto_rename`'s exists() check and `confirm_and_write`'s
            # atomic write. Surface as a write error rather than crash.
            # Codex review R1 2026-04-30.
            print(
                f"write error: rename collision (TOCTOU): {exc}; "
                "retry with --overwrite if appropriate",
                file=sys.stderr,
            )
            return EXIT_WRITE_ERROR
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"write error: {exc}", file=sys.stderr)
            return EXIT_WRITE_ERROR
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"write error: {exc}", file=sys.stderr)
        return EXIT_WRITE_ERROR

    print()
    print(result.summary())
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
