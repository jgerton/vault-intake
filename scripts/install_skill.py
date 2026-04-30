"""Install/sync the vault-intake skill artifacts to a Claude Code skills dir.

Lifts the skill from a dev-repo `uv run scripts/intake.py` to a live-invocable
`/vault-intake` at `~/.claude/skills/vault-intake/`. After install plus
the user opens a fresh Claude Code session, typing `/vault-intake` loads
the skill's `SKILL.md` and the wrappers run from the install location.

Allowlist (copied):
    SKILL.md
    pyproject.toml
    uv.lock
    src/vault_intake/    (recursive; `__pycache__` excluded)
    scripts/             (recursive; `__pycache__` excluded)

Anything outside the allowlist (tests/, references/, .git/, .venv/, stray
top-level files) is NOT copied. The destination is the source of truth for
the live skill; the dev repo is the source of truth for the install. Re-run
to sync; existing files at the destination are overwritten.

Usage:
    uv run scripts/install_skill.py
    uv run scripts/install_skill.py --dest /custom/path
    uv run scripts/install_skill.py --source /path/to/repo --dest /path

Exit codes:
    0  install succeeded
    2  source missing or required allowlisted file is absent
    4  destination write error (OSError during copy or mkdir)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DEST = Path.home() / ".claude" / "skills" / "vault-intake"

SYNC_FILES: tuple[str, ...] = ("SKILL.md", "pyproject.toml", "uv.lock")
SYNC_DIRS: tuple[str, ...] = ("src/vault_intake", "scripts")
EXCLUDE_NAMES: tuple[str, ...] = ("__pycache__",)


@dataclass(frozen=True)
class InstallResult:
    files_copied: int
    dirs_synced: int
    dest: Path


def _ignore_excluded(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDE_NAMES}


def install(source: Path, dest: Path) -> InstallResult:
    """Copy the allowlisted artifacts from `source` to `dest`.

    Validates the source layout up-front so a missing required file produces
    a clear error before anything is written. Replaces the directory subtrees
    in `dest` so stale files inside `src/vault_intake/` or `scripts/` from a
    prior install do not linger.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"source not found or not a directory: {source}")

    for name in SYNC_FILES:
        if not (source / name).is_file():
            raise FileNotFoundError(
                f"required source file missing: {(source / name)}"
            )
    for rel in SYNC_DIRS:
        if not (source / rel).is_dir():
            raise FileNotFoundError(
                f"required source directory missing: {(source / rel)}"
            )

    dest.mkdir(parents=True, exist_ok=True)
    files_copied = 0
    dirs_synced = 0

    for name in SYNC_FILES:
        shutil.copy2(source / name, dest / name)
        files_copied += 1

    for rel in SYNC_DIRS:
        src_dir = source / rel
        dst_dir = dest / rel
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir, ignore=_ignore_excluded)
        dirs_synced += 1
        for path in dst_dir.rglob("*"):
            if path.is_file():
                files_copied += 1

    return InstallResult(files_copied=files_copied, dirs_synced=dirs_synced, dest=dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_skill",
        description=(
            "Install / sync the vault-intake skill to a Claude Code skills "
            "directory."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="dev repo root (default: this script's parent repo)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"install destination (default: {DEFAULT_DEST})",
    )
    args = parser.parse_args(argv)

    try:
        result = install(args.source, args.dest)
    except FileNotFoundError as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"install write error: {exc}", file=sys.stderr)
        return 4

    print(
        f"installed: {result.files_copied} files, "
        f"{result.dirs_synced} dirs synced, "
        f"dest={result.dest}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
