"""Subprocess tests for `scripts/flush_nlm.py` manual drain command.

Locks the signed-off CLI surface for the manual drain helper invoked
after `notebooklm login`:

- Flags: `--vault`, `--nlm-command`. Env fallback for
  `VAULT_INTAKE_VAULT_PATH`.
- Exit codes: 0 on drain attempted (regardless of remaining queue),
  2 on config error.
- Per-entry log printed when `still_queued > 0`. Each entry includes
  `notebook_id`, `note_path`, `retry_count`.
- Corrupt queue files are counted as dropped (handled by the library).

Tests assume `notebooklm` CLI is NOT installed in the test environment;
the auth precheck inside `flush_nlm_queue` therefore raises
`FileNotFoundError`, which the library handles by returning all valid
items as `still_queued`. This keeps the tests deterministic without
mocking subprocess invocations.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "flush_nlm.py"


# ---------------------------------------------------------------------------
# Vault and queue builders
# ---------------------------------------------------------------------------


def _build_vault(tmp_path: Path, *, skip_nlm: bool = False) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for folder in ("sessions", "_inbox"):
        (vault / folder).mkdir()
    config = {
        "vault_path": str(vault),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [
            {"slug": "ops", "description": "operations"},
        ],
        "skip_notebooklm": skip_nlm,
    }
    yaml_block = yaml.safe_dump(config, sort_keys=False)
    (vault / "CLAUDE.md").write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\n" + yaml_block + "```\n",
        encoding="utf-8",
    )
    return vault


def _queue_filename(notebook_id: str, note_path: Path) -> str:
    key = f"{notebook_id}|{note_path}".encode("utf-8")
    return hashlib.sha1(key).hexdigest() + ".json"


def _add_queue_entry(
    vault: Path,
    *,
    notebook_id: str,
    note_path: Path,
    retry_count: int = 0,
    classification_primary: str = "ops",
    schema_version: int = 1,
) -> Path:
    queue_dir = vault / ".vault-intake" / "nlm_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "note_path": str(note_path),
        "notebook_id": notebook_id,
        "classification_primary": classification_primary,
        "retry_count": retry_count,
    }
    queue_file = queue_dir / _queue_filename(notebook_id, note_path)
    queue_file.write_text(json.dumps(payload), encoding="utf-8")
    return queue_file


def _run(
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Empty / missing queue
# ---------------------------------------------------------------------------


class TestEmptyQueue:
    def test_no_queue_dir_exits_0_with_zero_counts(self, tmp_path):
        vault = _build_vault(tmp_path)
        # No .vault-intake/nlm_queue/ directory created.
        result = _run(["--vault", str(vault)])
        assert result.returncode == 0, result.stderr
        assert "processed: 0" in result.stdout.lower()
        assert "still_queued: 0" in result.stdout.lower()
        assert "dropped: 0" in result.stdout.lower()

    def test_empty_queue_dir_exits_0(self, tmp_path):
        vault = _build_vault(tmp_path)
        (vault / ".vault-intake" / "nlm_queue").mkdir(parents=True)
        result = _run(["--vault", str(vault)])
        assert result.returncode == 0, result.stderr
        assert "still_queued: 0" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Queued items remain queued (no notebooklm CLI installed)
# ---------------------------------------------------------------------------


class TestStillQueued:
    def test_queue_with_items_keeps_queued_when_auth_check_fails(self, tmp_path):
        vault = _build_vault(tmp_path)
        note = vault / "sessions" / "n1.md"
        note.write_text("body", encoding="utf-8")
        _add_queue_entry(
            vault,
            notebook_id="nb-ops",
            note_path=note,
            retry_count=2,
        )
        # Use a non-existent command so auth precheck raises FileNotFoundError.
        result = _run(["--vault", str(vault), "--nlm-command", "definitely-not-a-real-cli"])
        assert result.returncode == 0, result.stderr
        assert "still_queued: 1" in result.stdout.lower()

    def test_per_entry_log_lists_notebook_note_and_retry_count(self, tmp_path):
        vault = _build_vault(tmp_path)
        note = vault / "sessions" / "n1.md"
        note.write_text("body", encoding="utf-8")
        _add_queue_entry(
            vault,
            notebook_id="nb-ops-id",
            note_path=note,
            retry_count=3,
        )
        result = _run(["--vault", str(vault), "--nlm-command", "definitely-not-a-real-cli"])
        assert result.returncode == 0, result.stderr
        # Per-entry log lines should appear in stdout. Each line carries
        # the three identifying fields per the signed-off UX.
        log_blob = result.stdout.lower()
        assert "notebook=nb-ops-id" in log_blob
        assert "note=" in log_blob
        assert "n1.md" in log_blob
        assert "retry_count=3" in log_blob

    def test_no_per_entry_log_when_queue_empty(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(["--vault", str(vault)])
        assert result.returncode == 0, result.stderr
        # When still_queued is 0, the per-entry log section is absent.
        assert "notebook=" not in result.stdout.lower()


# ---------------------------------------------------------------------------
# Corrupt queue files are dropped
# ---------------------------------------------------------------------------


class TestCorruptDropped:
    def test_corrupt_json_file_counted_as_dropped(self, tmp_path):
        vault = _build_vault(tmp_path)
        queue_dir = vault / ".vault-intake" / "nlm_queue"
        queue_dir.mkdir(parents=True)
        (queue_dir / "garbage.json").write_text("{ not valid json", encoding="utf-8")
        result = _run(["--vault", str(vault)])
        assert result.returncode == 0, result.stderr
        assert "dropped: 1" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Vault and config error paths
# ---------------------------------------------------------------------------


class TestVaultResolution:
    def test_missing_vault_arg_and_no_env_exits_2(self):
        result = _run([], env_extra={"VAULT_INTAKE_VAULT_PATH": ""})
        assert result.returncode == 2
        assert "vault" in result.stderr.lower()

    def test_env_fallback_used_when_no_flag(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run([], env_extra={"VAULT_INTAKE_VAULT_PATH": str(vault)})
        assert result.returncode == 0, result.stderr
        assert "still_queued: 0" in result.stdout.lower()

    def test_missing_claude_md_exits_2(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run(["--vault", str(vault)])
        assert result.returncode == 2

    def test_invalid_vault_config_exits_2(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "CLAUDE.md").write_text("# no config block\n", encoding="utf-8")
        result = _run(["--vault", str(vault)])
        assert result.returncode == 2
