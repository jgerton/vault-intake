"""CLI wrapper for `flush_nlm_queue`: manual NotebookLM retry-queue drain.

Usage:
    uv run scripts/flush_nlm.py [--vault PATH] [--nlm-command CMD]

Reads vault config from `<vault>/CLAUDE.md`, calls `flush_nlm_queue`,
then prints `processed/still_queued/dropped` counts. When any items
remain queued, prints a per-entry log line for each so the user can
see what is stuck and why.

Typical workflow:
    1. notebooklm login                # re-authenticate
    2. uv run scripts/flush_nlm.py     # drain the queue

Exit codes:
    0  drain attempted (regardless of remaining queue contents)
    2  config error (missing vault, malformed CLAUDE.md, missing
       CLAUDE.md)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vault_intake.config import Config, ConfigError, resolve_config
from vault_intake.notebooklm import _QUEUE_SCHEMA_VERSION, flush_nlm_queue


EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2

_QUEUE_SUBPATH = (".vault-intake", "nlm_queue")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flush_nlm.py",
        description="Drain the NotebookLM retry queue after `notebooklm login`.",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("VAULT_INTAKE_VAULT_PATH", ""),
        help="Vault root path. Falls back to env VAULT_INTAKE_VAULT_PATH.",
    )
    parser.add_argument(
        "--nlm-command",
        default="notebooklm",
        help="Override the notebooklm CLI command (default: notebooklm).",
    )
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    config = _resolve_config_from_args(args)
    if config is None:
        return EXIT_CONFIG_ERROR

    flush_result = flush_nlm_queue(config, nlm_command=args.nlm_command)

    print(
        f"processed: {flush_result.processed} / "
        f"still_queued: {flush_result.still_queued} / "
        f"dropped: {flush_result.dropped}"
    )

    if flush_result.still_queued > 0:
        print()
        print(f"{flush_result.still_queued} entry(ies) still queued:")
        for entry in _read_remaining_entries(config):
            print(
                f"- notebook={entry['notebook_id']} "
                f"note={entry['note_path']} "
                f"retry_count={entry['retry_count']}"
            )

    return EXIT_SUCCESS


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
        return resolve_config(claude_md)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return None
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _read_remaining_entries(config: Config) -> list[dict]:
    """Read whatever queue files remain after the drain pass.

    Best-effort: malformed or schema-mismatched payloads are skipped
    silently because the library's flush already counted them as
    dropped. We re-scan rather than extending `FlushResult` to surface
    entries; the queue size is bounded (sha1-keyed dedupe per
    notebook+note pair) so a re-scan stays cheap.

    Codex review R1 2026-04-30: schema_version match enforced so a
    stale-schema file that the library tried to drop but failed to
    unlink (transient OSError) does not surface in the per-entry log
    inconsistently with the FlushResult.dropped count.
    """
    queue_dir = config.vault_path.joinpath(*_QUEUE_SUBPATH)
    if not queue_dir.is_dir():
        return []
    entries: list[dict] = []
    for queue_file in sorted(queue_dir.glob("*.json")):
        try:
            payload = json.loads(queue_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != _QUEUE_SCHEMA_VERSION:
            continue
        notebook_id = payload.get("notebook_id")
        note_path = payload.get("note_path")
        retry_count = payload.get("retry_count", 0)
        if not isinstance(notebook_id, str) or not isinstance(note_path, str):
            continue
        entries.append(
            {
                "notebook_id": notebook_id,
                "note_path": note_path,
                "retry_count": retry_count,
            }
        )
    return entries


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
