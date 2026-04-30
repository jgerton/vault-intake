"""Subprocess tests for `scripts/intake.py` CLI wrapper.

Locks the signed-off CLI surface for the M1 dogfood loop:

- Flag set: `--vault`, `--input`, `--title`, `--source-type`,
  `--source-uri`, `--yes`, `--overwrite`, `--dry-run`, `--nlm-command`,
  `--skip-notebooklm`.
- Exit codes: 0 success / 1 user aborted / 2 config error / 3 pipeline
  error / 4 file write error.
- Stdin precedence: explicit `--input` wins over piped stdin; refuse
  TTY stdin with exit 2.
- Abort handling: EOF on a prompt and KeyboardInterrupt both exit 1
  (EOF is the portable proxy used here; signal.SIGINT delivery to
  Windows subprocesses is dicey enough that we rely on EOF for the
  cross-platform path).
- `--skip-notebooklm` short-circuits Step 9 in-process so the test
  suite does not need to mock the `notebooklm` CLI.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "intake.py"


# ---------------------------------------------------------------------------
# Vault builders
# ---------------------------------------------------------------------------


def _build_vault(
    tmp_path: Path,
    *,
    mode: str = "fixed_domains",
    skip_nlm: bool = True,
) -> Path:
    """Build a minimal vault with a CLAUDE.md config block.

    `skip_nlm` defaults to True so subprocess tests do not invoke the
    `notebooklm` CLI even when the wrapper does not pass the flag.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    for folder in (
        "sessions",
        "insights",
        "workflows",
        "prompts",
        "context",
        "projects",
        "references",
        "_inbox",
    ):
        (vault / folder).mkdir()

    if mode == "fixed_domains":
        config = {
            "vault_path": str(vault),
            "classification_mode": "fixed_domains",
            "routing_mode": "para",
            "domains": [
                {"slug": "ops", "description": "operations processes infrastructure"},
                {"slug": "branding", "description": "brand identity design messaging"},
                {"slug": "dev", "description": "software engineering code testing"},
            ],
            "skip_notebooklm": skip_nlm,
        }
    else:
        config = {
            "vault_path": str(vault),
            "classification_mode": "emergent",
            "routing_mode": "emergent",
            "skip_notebooklm": skip_nlm,
        }

    yaml_block = yaml.safe_dump(config, sort_keys=False)
    claude_md = vault / "CLAUDE.md"
    claude_md.write_text(
        "# Vault\n\n## Vault Config\n\n```yaml\n" + yaml_block + "```\n",
        encoding="utf-8",
    )
    return vault


def _run(
    args: list[str],
    *,
    stdin: str | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Invoke the script via the test interpreter.

    `sys.executable` keeps the call inside the active uv venv without
    spawning a fresh `uv run` (which would dominate test runtime).
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


_OPS_INPUT = (
    "# Ops infra check\n\n"
    "Quick ops note about infrastructure deployment process. "
    "We need to verify the ops processes for infrastructure rollout.\n"
)


# ---------------------------------------------------------------------------
# Happy path: --yes non-interactive
# ---------------------------------------------------------------------------


class TestYesHappyPath:
    def test_yes_writes_file_to_destination(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        # Spec table: (note, area) -> sessions/. Title heuristic produces
        # a slugged title; assert at least one .md file exists under
        # sessions/ and contains the body verbatim.
        sessions_dir = vault / "sessions"
        md_files = list(sessions_dir.glob("*.md"))
        assert len(md_files) == 1
        text = md_files[0].read_text(encoding="utf-8")
        assert "infrastructure deployment process" in text

    def test_yes_summary_printed_to_stdout(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        # Spec output contract fields.
        assert "Processed:" in result.stdout
        assert "Type:" in result.stdout
        assert "Destination:" in result.stdout

    def test_yes_with_title_override_uses_provided_title(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes", "--title", "my-custom-title"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        assert (vault / "sessions" / "my-custom-title.md").exists()


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_write_file(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes", "--dry-run"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        sessions_dir = vault / "sessions"
        assert list(sessions_dir.glob("*.md")) == []

    def test_dry_run_prints_summary(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes", "--dry-run"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        assert "Processed:" in result.stdout


# ---------------------------------------------------------------------------
# --input PATH and stdin precedence
# ---------------------------------------------------------------------------


class TestInputAndStdin:
    def test_input_path_reads_from_file(self, tmp_path):
        vault = _build_vault(tmp_path)
        input_file = tmp_path / "input.md"
        input_file.write_text(_OPS_INPUT, encoding="utf-8")
        result = _run(
            ["--vault", str(vault), "--yes", "--input", str(input_file)],
            stdin=None,  # no piped stdin
        )
        assert result.returncode == 0, result.stderr
        sessions_dir = vault / "sessions"
        assert len(list(sessions_dir.glob("*.md"))) == 1

    def test_input_path_takes_precedence_over_stdin(self, tmp_path):
        vault = _build_vault(tmp_path)
        input_file = tmp_path / "input.md"
        input_file.write_text(
            "# From file\n\nDev coding session about software testing.\n",
            encoding="utf-8",
        )
        # Stdin content would route to a different domain if used.
        result = _run(
            ["--vault", str(vault), "--yes", "--input", str(input_file)],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        # File content was used; assert via stdout summary mentioning dev.
        assert "dev" in result.stdout.lower()

    def test_piped_stdin_used_when_no_input_flag(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Vault resolution and config errors
# ---------------------------------------------------------------------------


class TestVaultResolution:
    def test_missing_vault_arg_and_no_env_exits_2(self, tmp_path):
        result = _run(
            ["--yes"],
            stdin=_OPS_INPUT,
            env_extra={"VAULT_INTAKE_VAULT_PATH": ""},
        )
        assert result.returncode == 2
        assert "vault" in result.stderr.lower()

    def test_env_fallback_used_when_no_flag(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--yes"],
            stdin=_OPS_INPUT,
            env_extra={"VAULT_INTAKE_VAULT_PATH": str(vault)},
        )
        assert result.returncode == 0, result.stderr

    def test_invalid_vault_config_exits_2(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "CLAUDE.md").write_text("# no config block here\n", encoding="utf-8")
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 2
        # Either ConfigError or vault-resolution error stems printed to stderr.
        assert "config" in result.stderr.lower() or "claude.md" in result.stderr.lower()

    def test_missing_claude_md_exits_2(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 2


# ---------------------------------------------------------------------------
# Collision handling
# ---------------------------------------------------------------------------


class TestCollision:
    def test_collision_without_overwrite_exits_4(self, tmp_path):
        vault = _build_vault(tmp_path)
        # First run lays down the file.
        first = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert first.returncode == 0, first.stderr

        # Second run with same title and no --overwrite collides.
        second = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert second.returncode == 4
        assert "exists" in second.stderr.lower() or "collision" in second.stderr.lower()

    def test_collision_with_overwrite_replaces_file(self, tmp_path):
        vault = _build_vault(tmp_path)
        first = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert first.returncode == 0, first.stderr

        # Overwrite with different content.
        new_content = (
            "# Different content\n\n"
            "Branding work on visual identity for the new launch campaign.\n"
        )
        second = _run(
            ["--vault", str(vault), "--yes", "--overwrite", "--title", "duplicate"],
            stdin=new_content,
        )
        assert second.returncode == 0, second.stderr
        # File now contains the second content.
        target = vault / "sessions" / "duplicate.md"
        assert target.exists()
        assert "Branding" in target.read_text(encoding="utf-8")

    def test_interactive_collision_overwrite_branch(self, tmp_path):
        """Interactive flow: pre-create the file, then run without
        `--yes` and answer write-confirmation `y` then collision-prompt
        `o`. The wrapper must overwrite and exit 0.
        """
        vault = _build_vault(tmp_path)
        first = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert first.returncode == 0, first.stderr

        input_file = tmp_path / "input.md"
        input_file.write_text(
            "# Different content\n\nBranding work on visual identity launch.\n",
            encoding="utf-8",
        )
        result = _run(
            [
                "--vault", str(vault),
                "--input", str(input_file),
                "--title", "duplicate",
            ],
            # write-confirmation `y`, collision-prompt `o`
            stdin="y\no\n",
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        target = vault / "sessions" / "duplicate.md"
        assert "Branding" in target.read_text(encoding="utf-8")

    def test_interactive_collision_rename_branch(self, tmp_path):
        """Interactive flow: pre-create the file, then run without
        `--yes` and answer write-confirmation `y` then collision-prompt
        `r`. The wrapper must auto-rename to `{title}-2.md` and exit 0.
        """
        vault = _build_vault(tmp_path)
        first = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert first.returncode == 0, first.stderr

        input_file = tmp_path / "input.md"
        input_file.write_text(_OPS_INPUT, encoding="utf-8")
        result = _run(
            [
                "--vault", str(vault),
                "--input", str(input_file),
                "--title", "duplicate",
            ],
            stdin="y\nr\n",
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        # Original file untouched; renamed file present.
        assert (vault / "sessions" / "duplicate.md").exists()
        assert (vault / "sessions" / "duplicate-2.md").exists()

    def test_interactive_collision_abort_branch(self, tmp_path):
        """Interactive flow: pre-create, run interactive, answer `y`
        for write then `a` for collision-prompt. Wrapper exits 1
        (user aborted) and leaves the existing file untouched.
        """
        vault = _build_vault(tmp_path)
        first = _run(
            ["--vault", str(vault), "--yes", "--title", "duplicate"],
            stdin=_OPS_INPUT,
        )
        assert first.returncode == 0, first.stderr
        target = vault / "sessions" / "duplicate.md"
        original_text = target.read_text(encoding="utf-8")

        input_file = tmp_path / "input.md"
        input_file.write_text(
            "# Wholly different content for the abort test.\n\n"
            "Branding work on visual identity launch.\n",
            encoding="utf-8",
        )
        result = _run(
            [
                "--vault", str(vault),
                "--input", str(input_file),
                "--title", "duplicate",
            ],
            stdin="y\na\n",
        )
        assert result.returncode == 1, (result.stdout, result.stderr)
        # Original file content preserved.
        assert target.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# --title validation
# ---------------------------------------------------------------------------


class TestTitleValidation:
    def test_empty_title_exits_2(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes", "--title", ""],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 2
        assert "title" in result.stderr.lower()

    def test_whitespace_title_exits_2(self, tmp_path):
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes", "--title", "   "],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 2
        assert "title" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Abort handling: EOF on stdin during prompt acts as user abort
# ---------------------------------------------------------------------------


class TestAbort:
    def test_eof_during_write_confirmation_exits_1(self, tmp_path):
        """Without --yes, the wrapper prompts for write confirmation.
        Closing stdin (no piped input remains for the prompt) raises
        EOFError from `input()`; the wrapper must treat this as user
        abort and exit 1 without writing.

        We simulate this by piping the input text via `--input` and
        leaving stdin empty. Any prompt-time `input()` call hits EOF.
        """
        vault = _build_vault(tmp_path)
        input_file = tmp_path / "input.md"
        input_file.write_text(_OPS_INPUT, encoding="utf-8")
        result = _run(
            # No --yes so the wrapper attempts to prompt; stdin is
            # piped-empty so the first prompt EOFs.
            ["--vault", str(vault), "--input", str(input_file)],
            stdin="",
        )
        assert result.returncode == 1, (result.stdout, result.stderr)
        # File must not have been written.
        sessions_dir = vault / "sessions"
        assert list(sessions_dir.glob("*.md")) == []


# ---------------------------------------------------------------------------
# Structured-question answer flow (interactive)
# ---------------------------------------------------------------------------


class TestStructuredAnswerFlow:
    def test_title_flag_skips_title_prompt(self, tmp_path):
        """With `--title` provided, the wrapper does not emit a title
        prompt to stdout. We verify by running interactively (no `--yes`)
        with stdin providing only a 'y' for the write confirmation; if
        the wrapper still emitted a title prompt, it would consume the
        'y' and fail the write confirmation.
        """
        vault = _build_vault(tmp_path)
        input_file = tmp_path / "input.md"
        input_file.write_text(_OPS_INPUT, encoding="utf-8")
        # Stdin: 'y' for write confirmation only.
        result = _run(
            [
                "--vault", str(vault),
                "--input", str(input_file),
                "--title", "skip-title-prompt",
            ],
            stdin="y\n",
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert (vault / "sessions" / "skip-title-prompt.md").exists()

    def test_yes_flag_accepts_all_suggestions_no_prompts(self, tmp_path):
        """With `--yes`, no prompt is rendered and stdin is not
        consumed for prompts. A full input via stdin is the input
        text, not a prompt answer."""
        vault = _build_vault(tmp_path)
        result = _run(
            ["--vault", str(vault), "--yes"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        # The wrapper should not print prompt arrows in --yes mode.
        assert "> " not in result.stdout


# ---------------------------------------------------------------------------
# --skip-notebooklm
# ---------------------------------------------------------------------------


class TestSkipNotebookLM:
    def test_skip_notebooklm_flag_short_circuits_step_9(self, tmp_path):
        """`--skip-notebooklm` overrides config.skip_notebooklm=True for
        the run. The wrapper must pass the override into Config and the
        orchestrator must skip Step 9 cleanly. We assert via stdout
        summary mentioning skipped."""
        vault = _build_vault(tmp_path, skip_nlm=False)  # config says NOT to skip
        result = _run(
            ["--vault", str(vault), "--yes", "--skip-notebooklm"],
            stdin=_OPS_INPUT,
        )
        assert result.returncode == 0, result.stderr
        assert "NotebookLM: skipped" in result.stdout
