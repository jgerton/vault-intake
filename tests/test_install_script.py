"""Subprocess tests for `scripts/install_skill.py` skill install/sync.

Locks the signed-off contract for the install/sync mechanism that lifts
vault-intake from a dev-repo `uv run scripts/intake.py` to a live-invocable
`/vault-intake` skill at `~/.claude/skills/vault-intake/`.

Sync list (allowlist):
- `SKILL.md`
- `pyproject.toml`
- `uv.lock`
- `src/vault_intake/` (recursive; `__pycache__` excluded)
- `scripts/` (recursive; `__pycache__` excluded)

Anything outside the allowlist is NOT copied (tests/, references/, .git/,
.venv/, stray files at repo root). Re-running overwrites destination files
in place; safe to invoke repeatedly.

Tests use a synthetic source tree built in `tmp_path` so they do not depend
on the live repo's exact contents and never touch the real installation
location at `~/.claude/skills/vault-intake/`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_skill.py"


# ---------------------------------------------------------------------------
# Fake source tree builder
# ---------------------------------------------------------------------------


def _build_source(tmp_path: Path) -> Path:
    """Build a minimal but realistic vault-intake-shaped source tree.

    Contains:
    - allowlist: SKILL.md, pyproject.toml, uv.lock, src/vault_intake/*.py, scripts/*.py
    - excluded: tests/, references/, .git/, .venv/, __pycache__/, stray files
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("# vault-intake\n\nSkill body content.\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "vault-intake"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (source / "uv.lock").write_text("# uv lockfile\nversion = 1\n", encoding="utf-8")

    pkg = source / "src" / "vault_intake"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "config.py").write_text("# config module\n", encoding="utf-8")
    (pkg / "orchestrator.py").write_text("# orchestrator module\n", encoding="utf-8")
    pycache_pkg = pkg / "__pycache__"
    pycache_pkg.mkdir()
    (pycache_pkg / "config.cpython-312.pyc").write_bytes(b"\x00\x00\x00")

    scripts = source / "scripts"
    scripts.mkdir()
    (scripts / "intake.py").write_text("# intake CLI wrapper\n", encoding="utf-8")
    (scripts / "flush_nlm.py").write_text("# flush_nlm CLI wrapper\n", encoding="utf-8")
    (scripts / "resolve_config.py").write_text("# resolve_config helper\n", encoding="utf-8")
    (scripts / "install_skill.py").write_text("# install script self\n", encoding="utf-8")
    pycache_scripts = scripts / "__pycache__"
    pycache_scripts.mkdir()
    (pycache_scripts / "intake.cpython-312.pyc").write_bytes(b"\x00\x00\x00")

    tests = source / "tests"
    tests.mkdir()
    (tests / "test_install_script.py").write_text("# excluded\n", encoding="utf-8")

    refs = source / "references"
    refs.mkdir()
    (refs / "spec.md").write_text("# excluded reference\n", encoding="utf-8")

    git = source / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/feat/m1\n", encoding="utf-8")

    venv = source / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("# excluded\n", encoding="utf-8")

    (source / "README.md").write_text("# stray top-level file\n", encoding="utf-8")
    (source / "uv.lock.bak").write_text("# stray top-level file\n", encoding="utf-8")

    return source


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
# Sync list (allowlisted artifacts must reach the destination)
# ---------------------------------------------------------------------------


class TestSyncList:
    def test_skill_md_copied_verbatim(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        src_bytes = (source / "SKILL.md").read_bytes()
        dst_bytes = (dest / "SKILL.md").read_bytes()
        assert src_bytes == dst_bytes

    def test_pyproject_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert (dest / "pyproject.toml").is_file()
        assert (dest / "pyproject.toml").read_bytes() == (source / "pyproject.toml").read_bytes()

    def test_uv_lock_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert (dest / "uv.lock").is_file()
        assert (dest / "uv.lock").read_bytes() == (source / "uv.lock").read_bytes()

    def test_src_package_copied_recursively(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert (dest / "src" / "vault_intake" / "__init__.py").is_file()
        assert (dest / "src" / "vault_intake" / "config.py").is_file()
        assert (dest / "src" / "vault_intake" / "orchestrator.py").is_file()

    def test_scripts_dir_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        for name in ("intake.py", "flush_nlm.py", "resolve_config.py", "install_skill.py"):
            assert (dest / "scripts" / name).is_file(), name


# ---------------------------------------------------------------------------
# Exclusions (anything outside the allowlist must NOT reach the destination)
# ---------------------------------------------------------------------------


class TestExclusions:
    def test_tests_dir_not_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / "tests").exists()

    def test_references_dir_not_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / "references").exists()

    def test_dot_git_not_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / ".git").exists()

    def test_dot_venv_not_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / ".venv").exists()

    def test_pycache_excluded_from_src_package(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / "src" / "vault_intake" / "__pycache__").exists()

    def test_pycache_excluded_from_scripts(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / "scripts" / "__pycache__").exists()

    def test_stray_top_level_files_not_copied(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert not (dest / "README.md").exists()
        assert not (dest / "uv.lock.bak").exists()


# ---------------------------------------------------------------------------
# Idempotency (re-running must overwrite without error)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_reinstall_overwrites_modified_skill_md(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"

        first = _run(["--source", str(source), "--dest", str(dest)])
        assert first.returncode == 0, first.stderr

        # Simulate a stale or hand-edited destination SKILL.md
        (dest / "SKILL.md").write_text("MODIFIED IN PLACE\n", encoding="utf-8")

        second = _run(["--source", str(source), "--dest", str(dest)])
        assert second.returncode == 0, second.stderr
        assert (dest / "SKILL.md").read_text(encoding="utf-8") == (
            source / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_reinstall_overwrites_modified_src_module(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"

        first = _run(["--source", str(source), "--dest", str(dest)])
        assert first.returncode == 0, first.stderr

        (dest / "src" / "vault_intake" / "config.py").write_text(
            "TAMPERED\n", encoding="utf-8"
        )

        second = _run(["--source", str(source), "--dest", str(dest)])
        assert second.returncode == 0, second.stderr
        assert (dest / "src" / "vault_intake" / "config.py").read_text(
            encoding="utf-8"
        ) == (source / "src" / "vault_intake" / "config.py").read_text(encoding="utf-8")

    def test_creates_dest_directory_when_missing(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "deeply" / "nested" / "skills" / "vault-intake"
        assert not dest.exists()
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        assert dest.is_dir()
        assert (dest / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCLISurface:
    def test_help_exits_0(self, tmp_path):
        result = _run(["--help"])
        assert result.returncode == 0, result.stderr
        assert "install" in result.stdout.lower()

    def test_missing_source_exits_2(self, tmp_path):
        bogus_source = tmp_path / "does-not-exist"
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(bogus_source), "--dest", str(dest)])
        assert result.returncode == 2, (result.returncode, result.stderr)
        assert "source" in result.stderr.lower()

    def test_source_missing_required_file_exits_2(self, tmp_path):
        source = _build_source(tmp_path)
        # Remove a required allowlisted file from the source so the install
        # short-circuits with a clear error rather than silently producing a
        # partial skill.
        (source / "pyproject.toml").unlink()
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 2, (result.returncode, result.stderr)
        assert "pyproject.toml" in result.stderr

    def test_summary_printed_on_success(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        result = _run(["--source", str(source), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr
        # Summary line communicates what was synced and where.
        lower = result.stdout.lower()
        assert "installed" in lower
        assert str(dest).lower() in lower
