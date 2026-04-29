"""Tests for the resolve_config CLI wrapper."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "resolve_config.py"


def run_cli(claude_md_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(claude_md_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_prints_json_for_valid_config(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "fixed_domains",
        "routing_mode": "para",
        "domains": [{"slug": "alpha", "description": "x"}],
    })

    result = run_cli(claude_md)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "fixed_domains"
    assert payload["domains"] == [{"slug": "alpha", "description": "x"}]
    assert payload["language"] == "en"


def test_cli_exits_nonzero_on_config_error(write_claude_md, tmp_path):
    claude_md = write_claude_md({
        "vault_path": str(tmp_path),
        "classification_mode": "emergent",
        "routing_mode": "para",
    })

    result = run_cli(claude_md)

    assert result.returncode != 0
    assert "unsupported" in result.stderr


def test_cli_exits_nonzero_when_no_arg_given():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()
