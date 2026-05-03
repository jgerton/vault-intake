"""Bootstrap: ensure standard vault directory structure exists.

Called before the first intake run (or on-demand via `vault-intake init`)
to guarantee that all expected folders are present. Idempotent: safe to
call on an already-initialized vault.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config

# Directories created unconditionally (mode-agnostic). `sessions` is not
# here; domain-scoped <domain>/sessions/ dirs are created per domain instead.
_STANDARD_DIRS: tuple[str, ...] = (
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
    """Ensure standard vault directories exist under config.vault_path.

    Returns a list of all directory paths that were ensured (whether they
    already existed or were newly created). Never deletes or modifies
    existing content.

    Raises ValueError if vault_path already exists as a file (not a
    directory), since child directory creation would fail non-obviously.
    """
    vault = config.vault_path
    if vault.exists() and not vault.is_dir():
        raise ValueError(
            f"vault_path {vault!r} exists but is not a directory"
        )

    ensured: list[Path] = []

    for name in _STANDARD_DIRS:
        d = vault / name
        d.mkdir(parents=True, exist_ok=True)
        ensured.append(d)

    for domain in config.domains:
        sessions_dir = vault / domain.slug / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        ensured.append(sessions_dir)

    queue_dir = vault / _QUEUE_DIR
    queue_dir.mkdir(parents=True, exist_ok=True)
    ensured.append(queue_dir)

    return ensured
