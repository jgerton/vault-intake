"""Install/sync the vault-intake skill artifacts to a Claude Code skills dir.

Lifts the skill from a dev-repo `uv run scripts/intake.py` to a live-invocable
`/vault-intake` at `~/.claude/skills/vault-intake/`. After install plus
the user opens a fresh Claude Code session, typing `/vault-intake` loads
the skill's `SKILL.md` and the wrappers run from the install location.

Allowlist (copied):
    SKILL.md
    pyproject.toml
    uv.lock
    src/vault_intake/    (recursive; `__pycache__` and symlinks excluded)
    scripts/             (recursive; `__pycache__` and symlinks excluded)

Anything outside the allowlist (tests/, references/, .git/, .venv/, stray
top-level files) is NOT copied.

Containment contract:
- Symlinks are refused at the top-level allowlist (a symlinked SKILL.md
  rejects with exit 2) and skipped inside synced dirs (so an accidental or
  malicious symlink under `scripts/` cannot pull outside-repo content into
  the install).
- The install owns the allowlist only: the entire `src/vault_intake/` and
  `scripts/` subtrees at the destination are replaced on each run (so
  removed dev files are reflected at the install), plus the named top-level
  files. Files OUTSIDE the allowlist at the destination root or in other
  subtrees are preserved untouched (the install does not nuke user-placed
  content at the skill directory).

Idempotent: re-run to sync after dev changes; existing allowlisted files at
the destination are overwritten in place.

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


def _ignore_excluded(directory: str, names: list[str]) -> set[str]:
    """`shutil.copytree` ignore callback.

    Excludes `__pycache__` directories and any entry that is itself a
    symlink (regardless of target). Skipping symlinks here is a containment
    defense: `shutil.copytree(symlinks=False)` would otherwise dereference a
    symlink under `scripts/` or `src/vault_intake/` and copy outside-repo
    content into the install.
    """
    ignored: set[str] = set()
    for name in names:
        if name in EXCLUDE_NAMES:
            ignored.add(name)
            continue
        if (Path(directory) / name).is_symlink():
            ignored.add(name)
    return ignored


def install(source: Path, dest: Path) -> InstallResult:
    """Copy the allowlisted artifacts from `source` to `dest`.

    Validates the source layout up-front so a missing required file produces
    a clear error before anything is written. Rejects symlinked top-level
    allowlist entries so `Path.is_file()` (which dereferences symlinks)
    cannot let a symlinked SKILL.md leak outside-repo content. Replaces the
    directory subtrees in `dest` so stale files inside `src/vault_intake/`
    or `scripts/` from a prior install do not linger; non-allowlist content
    elsewhere at `dest` is left untouched.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"source not found or not a directory: {source}")

    for name in SYNC_FILES:
        candidate = source / name
        if candidate.is_symlink():
            raise FileNotFoundError(
                f"required source file is a symlink, refusing: {candidate}"
            )
        if not candidate.is_file():
            raise FileNotFoundError(
                f"required source file missing: {candidate}"
            )
    for rel in SYNC_DIRS:
        candidate = source / rel
        if candidate.is_symlink():
            raise FileNotFoundError(
                f"required source directory is a symlink, refusing: {candidate}"
            )
        if not candidate.is_dir():
            raise FileNotFoundError(
                f"required source directory missing: {candidate}"
            )

    dest.mkdir(parents=True, exist_ok=True)
    files_copied = 0
    dirs_synced = 0

    for name in SYNC_FILES:
        dst_path = dest / name
        # If the destination entry is itself a symlink, unlink it first so
        # `copy2` writes a fresh regular file at the install location rather
        # than dereferencing the symlink and clobbering its outside target.
        # `Path.unlink()` on a symlink removes the symlink only; the target
        # is untouched.
        if dst_path.is_symlink():
            dst_path.unlink()
        shutil.copy2(source / name, dst_path)
        files_copied += 1

    for rel in SYNC_DIRS:
        src_dir = source / rel
        dst_dir = dest / rel
        # Same defense for synced directories: a symlinked `dest/scripts`
        # must be replaced as a symlink, not recursed into and rmtree'd
        # (which could delete content at the symlink's outside target).
        if dst_dir.is_symlink():
            dst_dir.unlink()
        elif dst_dir.exists():
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
