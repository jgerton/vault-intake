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

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_skill.py"


def _load_install_module():
    """Import scripts/install_skill.py as a module for in-process tests.

    `scripts/` is not on `sys.path` per the project's pytest config (only
    `src/` is), so we load by file location for the unit tests that need
    `monkeypatch` against the script's `shutil` symbol. The module must be
    registered in `sys.modules` BEFORE `exec_module` because the
    `@dataclass` decorator on `InstallResult` looks up its module by name
    via `sys.modules[cls.__module__].__dict__`; without registration the
    decorator raises `AttributeError`.
    """
    spec = importlib.util.spec_from_file_location("install_skill", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def symlink_supported(tmp_path):
    """Skip the test when the OS or user lacks symlink-creation permission.

    Windows requires admin or Developer Mode for `os.symlink`; on a default
    user account the call raises `OSError(WinError 1314)`. The library code
    being exercised is platform-portable, so the test is best-effort: if
    symlinks cannot be created in the fixture, the corresponding behavior
    is still defended in production but verified manually elsewhere.
    """
    probe_src = tmp_path / "_symlink_probe_src"
    probe_src.write_text("probe", encoding="utf-8")
    probe_dst = tmp_path / "_symlink_probe_dst"
    try:
        probe_dst.symlink_to(probe_src)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unsupported on this platform / user: {exc}")


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


# ---------------------------------------------------------------------------
# Symlink containment (the allowlist must remain a real boundary; symlinks
# must not let outside-repo content leak into the install).
# ---------------------------------------------------------------------------


class TestSymlinkContainment:
    def test_symlinked_top_level_file_rejected(self, tmp_path, symlink_supported):
        source = _build_source(tmp_path)
        outside = tmp_path / "outside_secret.md"
        outside.write_text(
            "# outside content; must not leak into the install\n",
            encoding="utf-8",
        )
        # Replace SKILL.md with a symlink pointing at outside content.
        (source / "SKILL.md").unlink()
        (source / "SKILL.md").symlink_to(outside)
        dest = tmp_path / "skills" / "vault-intake"

        result = _run(["--source", str(source), "--dest", str(dest)])

        assert result.returncode == 2, (result.returncode, result.stderr)
        assert "symlink" in result.stderr.lower()
        # And nothing should have been written at the destination.
        assert not (dest / "SKILL.md").exists()

    def test_symlink_inside_synced_dir_skipped(self, tmp_path, symlink_supported):
        source = _build_source(tmp_path)
        outside = tmp_path / "outside_secret.txt"
        outside.write_text(
            "secret content; must not leak into the install\n",
            encoding="utf-8",
        )
        # Plant a symlink under scripts/ pointing at outside content.
        (source / "scripts" / "leaked.py").symlink_to(outside)
        dest = tmp_path / "skills" / "vault-intake"

        result = _run(["--source", str(source), "--dest", str(dest)])

        assert result.returncode == 0, result.stderr
        # The symlink must NOT have been copied (neither as symlink nor as
        # the dereferenced content).
        leaked = dest / "scripts" / "leaked.py"
        assert not leaked.exists(), (
            "symlink under scripts/ leaked into install"
        )
        # The real allowlisted scripts files are still present.
        assert (dest / "scripts" / "intake.py").is_file()


# ---------------------------------------------------------------------------
# Containment contract: install owns the allowlist only. Non-allowlist
# content placed at the destination root or in other subtrees is preserved
# untouched (the install is not a hard mirror; user files are not nuked).
# ---------------------------------------------------------------------------


class TestNonAllowlistPreservation:
    def test_user_file_at_dest_root_preserved_across_reinstall(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        first = _run(["--source", str(source), "--dest", str(dest)])
        assert first.returncode == 0, first.stderr

        user_file = dest / "user_notes.md"
        user_file.write_text("user content; not from install\n", encoding="utf-8")

        second = _run(["--source", str(source), "--dest", str(dest)])
        assert second.returncode == 0, second.stderr
        assert user_file.read_text(encoding="utf-8") == (
            "user content; not from install\n"
        )

    def test_user_dir_at_dest_root_preserved_across_reinstall(self, tmp_path):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        first = _run(["--source", str(source), "--dest", str(dest)])
        assert first.returncode == 0, first.stderr

        # Stale dir from a hypothetical prior version OR user-placed content.
        stale = dest / "tests"
        stale.mkdir()
        (stale / "old.py").write_text("# stale or user-placed\n", encoding="utf-8")

        second = _run(["--source", str(source), "--dest", str(dest)])
        assert second.returncode == 0, second.stderr
        assert stale.exists()
        assert (stale / "old.py").read_text(encoding="utf-8") == (
            "# stale or user-placed\n"
        )


# ---------------------------------------------------------------------------
# Exit code 4 (destination write error). Exercised in-process via
# `monkeypatch` rather than subprocess so we can simulate the OSError
# branch deterministically.
# ---------------------------------------------------------------------------


class TestExitCodeFour:
    def test_oserror_during_copy_returns_exit_4(self, tmp_path, monkeypatch, capsys):
        source = _build_source(tmp_path)
        dest = tmp_path / "skills" / "vault-intake"
        install_skill = _load_install_module()

        def boom(_src, _dst, *_a, **_kw):
            raise PermissionError("simulated permission denied at copy time")

        monkeypatch.setattr(install_skill.shutil, "copy2", boom)
        rc = install_skill.main(
            ["--source", str(source), "--dest", str(dest)]
        )

        assert rc == 4
        captured = capsys.readouterr()
        assert "install write error" in captured.err.lower()
        assert "permission denied" in captured.err.lower()
