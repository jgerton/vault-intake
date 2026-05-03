"""Bootstrap: ensure standard vault directory structure exists.

Called before the first intake run (or on-demand via `vault-intake init`)
to guarantee that all expected folders are present. Idempotent: safe to
call on an already-initialized vault.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config

# Directories created unconditionally (mode-agnostic).
_STANDARD_DIRS: tuple[str, ...] = (
    "sessions",
    "insights",
    "workflows",
    "prompts",
    "references",
    "projects",
    "context",
    "_inbox",
    "inbox",
)

_QUEUE_DIR = Path(".vault-intake") / "nlm_queue"


def bootstrap_vault(config: Config) -> list[Path]:
    """Create standard vault directories under config.vault_path.

    Returns a list of all directory paths that were ensured (whether they
    already existed or were newly created). Never deletes or modifies
    existing content.
    """
    vault = config.vault_path
    created: list[Path] = []

    for name in _STANDARD_DIRS:
        d = vault / name
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)

    queue_dir = vault / _QUEUE_DIR
    queue_dir.mkdir(parents=True, exist_ok=True)
    created.append(queue_dir)

    return created
