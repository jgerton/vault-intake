"""CLI wrapper for Step 1: resolve and validate vault config.

Usage:
    uv run scripts/resolve_config.py <path-to-CLAUDE.md>

Prints resolved config as JSON to stdout on success.
On failure, prints a message to stderr and exits non-zero.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from vault_intake.config import ConfigError, resolve_config


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: resolve_config.py <path-to-CLAUDE.md>", file=sys.stderr)
        return 2

    try:
        config = resolve_config(Path(argv[1]))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "vault_path": str(config.vault_path),
        "mode": config.mode,
        "domains": [
            {"slug": d.slug, "description": d.description} for d in config.domains
        ],
        "notebook_map": config.notebook_map,
        "language": config.language,
        "skip_notebooklm": config.skip_notebooklm,
        "refinement_enabled": config.refinement_enabled,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
