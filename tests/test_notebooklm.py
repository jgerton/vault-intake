"""Tests for Step 9: NotebookLM integration (opt-in, graceful).

Per build spec lines 216-226: when `skip_notebooklm: false` and
`notebook_map` has an entry for the note's classification key, look up
the notebook ID, check source count (warn if >= 45/50), add the note
as a source, parse the returned source_id, and update frontmatter.
On failure, log and continue (never block).

Step 9 is mode-agnostic: both `fixed_domains` and `emergent` use
`classification.primary` as the lookup key in `config.notebook_map`.

Two extensions over the bare spec, signed off 2026-04-30:

1. Auth precheck before `source add` via `notebooklm auth check --test`.
   If auth has expired (the underlying Google session cookies, not the
   self-refreshing CSRF token), the call would fail anyway. Detecting
   upfront saves wasted subprocess time.

2. Persistent retry queue at `<vault>/.vault-intake/nlm_queue/`. When
   the precheck fails or runtime returns an auth-error pattern
   ("Unauthorized", "redirect", "CSRF token missing"), serialize the
   pending action to a versioned JSON file. A separate
   `flush_nlm_queue()` library function drains the queue once the user
   re-runs `notebooklm login`. Non-auth failures (timeout, JSON parse
   error, source-count exhausted) are NOT queued because re-auth would
   not recover them.

All subprocess calls are mocked at the test boundary via
`unittest.mock.patch("vault_intake.notebooklm.subprocess.run")`; tests
never make real network calls or invoke the real CLI.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from vault_intake.classify import ClassificationResult
from vault_intake.config import Config, Domain
from vault_intake.frontmatter import Frontmatter
from vault_intake.notebooklm import (
    FlushResult,
    NotebookLMResult,
    flush_nlm_queue,
    integrate_notebooklm,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_config(
    *,
    mode: str = "fixed_domains",
    vault_path: Path | None = None,
    notebook_map: dict[str, str] | None = None,
    skip_notebooklm: bool = False,
) -> Config:
    return Config(
        vault_path=vault_path or Path("/tmp/vault-stub"),
        mode=mode,  # type: ignore[arg-type]
        domains=(
            Domain(slug="ops", description="Operations and processes."),
            Domain(slug="branding", description="Brand identity and design."),
            Domain(slug="dev", description="Software development and engineering."),
        ) if mode == "fixed_domains" else (),
        notebook_map=MappingProxyType(
            notebook_map if notebook_map is not None else {
                "ops": "nb-ops-id",
                "branding": "nb-branding-id",
            }
        ),
        language="en",
        skip_notebooklm=skip_notebooklm,
        refinement_enabled=True,
        classification_confidence_threshold=0.6,
    )


def _make_classification(primary: str = "ops", mode: str = "fixed_domains") -> ClassificationResult:
    return ClassificationResult(
        primary=primary,
        secondary=(),
        confidence=0.8,
        uncertain=False,
        mode=mode,  # type: ignore[arg-type]
    )


def _make_frontmatter(
    *,
    title: str = "ops-onboarding-notes",
    domain: str = "ops",
    captured_at: str = "2026-04-30",
) -> Frontmatter:
    return Frontmatter(
        schema_version="1.0",
        source_type="paste",
        source_uri="",
        captured_at=captured_at,
        processed_by="/vault-intake",
        confidence=0.8,
        original_ref="",
        title=title,
        date=captured_at,
        type="session",
        domain=domain,
        tags=(domain,),
        notebook="",
        source_id="",
        project="",
    )


def _make_note(tmp_path: Path, name: str = "note.md", body: str = "# Note\nbody.\n") -> Path:
    note = tmp_path / name
    note.write_text(body, encoding="utf-8")
    return note


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _route_subprocess(
    *,
    auth_check: subprocess.CompletedProcess | Exception | None = None,
    source_list: subprocess.CompletedProcess | Exception | None = None,
    source_add: subprocess.CompletedProcess | Exception | None = None,
):
    """Build a subprocess.run replacement that dispatches by command shape.

    Each arg is the desired return value (or exception to raise) when the
    matching command is invoked. Defaults: auth_check returns code 0 with
    a healthy line, source_list returns an empty JSON list (count=0),
    source_add returns a JSON object with id="src-abc".
    """
    auth = auth_check if auth_check is not None else _completed(stdout="ok\n")
    listing = source_list if source_list is not None else _completed(
        stdout=json.dumps([])
    )
    add = source_add if source_add is not None else _completed(
        stdout=json.dumps({"id": "src-abc", "title": "ops-onboarding-notes"})
    )

    def fake_run(cmd, *args, **kwargs):
        # cmd is a list like ["notebooklm", "auth", "check", "--test"].
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "auth check" in joined:
            target = auth
        elif "source list" in joined:
            target = listing
        elif "source add" in joined:
            target = add
        else:
            raise AssertionError(f"unexpected subprocess call: {cmd!r}")
        if isinstance(target, Exception):
            raise target
        return target

    return fake_run


# ---------------------------------------------------------------------------
# Round 1: skipped paths (no subprocess calls)
# ---------------------------------------------------------------------------


def test_skipped_when_skip_notebooklm_true(tmp_path):
    config = _make_config(skip_notebooklm=True)
    note = _make_note(tmp_path)

    with patch("vault_intake.notebooklm.subprocess.run") as run:
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.skipped is True
    assert result.failed is False
    assert result.queued is False
    assert result.source_id is None
    assert result.notebook_id is None
    assert "skip_notebooklm" in result.reason
    run.assert_not_called()


def test_skipped_when_no_mapping_for_classification_primary(tmp_path):
    config = _make_config(notebook_map={"branding": "nb-branding-id"})
    note = _make_note(tmp_path)

    with patch("vault_intake.notebooklm.subprocess.run") as run:
        result = integrate_notebooklm(
            classification=_make_classification(primary="ops"),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.skipped is True
    assert result.failed is False
    assert result.queued is False
    assert result.notebook_id is None
    assert "no mapping" in result.reason.lower()
    run.assert_not_called()


def test_skipped_when_note_path_is_none_dry_run():
    config = _make_config()

    with patch("vault_intake.notebooklm.subprocess.run") as run:
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=None,
        )

    assert result.skipped is True
    assert result.failed is False
    assert "dry-run" in result.reason.lower()
    run.assert_not_called()


def test_skipped_when_cli_missing(tmp_path):
    config = _make_config()
    note = _make_note(tmp_path)

    with patch(
        "vault_intake.notebooklm.subprocess.run",
        side_effect=FileNotFoundError("notebooklm"),
    ):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.skipped is True
    assert result.failed is False
    assert result.queued is False
    assert "cli" in result.reason.lower() and "not available" in result.reason.lower()


# ---------------------------------------------------------------------------
# Round 2: auth precheck
# ---------------------------------------------------------------------------


def test_auth_precheck_pass_proceeds_to_source_add(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is False
    assert result.queued is False
    assert result.source_id == "src-abc"
    # Expect three subprocess calls: auth check, source list, source add.
    assert run.call_count == 3


def test_auth_precheck_uses_auth_check_with_test_flag(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    first_call = run.call_args_list[0]
    cmd = first_call.args[0] if first_call.args else first_call.kwargs.get("args")
    assert cmd[0] == "notebooklm"
    assert "auth" in cmd
    assert "check" in cmd
    assert "--test" in cmd


def test_auth_precheck_fail_writes_to_queue_and_returns_queued(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)
    failed_auth = _completed(returncode=1, stderr="Unauthorized\n")

    fake = _route_subprocess(auth_check=failed_auth)
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is True
    assert result.source_id is None
    assert result.notebook_id == "nb-ops-id"
    assert "auth" in result.reason.lower()

    # Source add must not be attempted after a failed precheck.
    cmds = [c.args[0] for c in run.call_args_list]
    assert not any("add" in cmd for cmd in cmds)

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert queue_dir.is_dir()
    files = list(queue_dir.glob("*.json"))
    assert len(files) == 1


def test_auth_precheck_uses_PYTHONIOENCODING_utf8(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    for call in run.call_args_list:
        env = call.kwargs.get("env") or {}
        assert env.get("PYTHONIOENCODING") == "utf-8"


# ---------------------------------------------------------------------------
# Round 3: success path with source_id parsing
# ---------------------------------------------------------------------------


def test_success_returns_source_id_parsed_from_json(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=_completed(stdout=json.dumps({"id": "src-zzz", "title": "x"}))
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.source_id == "src-zzz"
    assert result.notebook_id == "nb-ops-id"
    assert result.failed is False
    assert result.skipped is False


def test_success_supports_source_id_field_alias(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    # Some CLI versions might emit `source_id` instead of `id`. Defensive parser.
    fake = _route_subprocess(
        source_add=_completed(stdout=json.dumps({"source_id": "src-alias", "title": "x"}))
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.source_id == "src-alias"


def test_source_add_invokes_with_notebook_id_and_json_flag(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    add_call = next(c for c in run.call_args_list if "add" in c.args[0])
    cmd = add_call.args[0]
    assert "notebooklm" == cmd[0]
    assert "source" in cmd and "add" in cmd
    assert "-n" in cmd
    assert "nb-ops-id" in cmd
    assert "--json" in cmd
    assert str(note) in cmd


# ---------------------------------------------------------------------------
# Round 4: source-count check and warnings
# ---------------------------------------------------------------------------


def test_source_count_below_threshold_no_warning(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_list=_completed(stdout=json.dumps([{"id": f"src-{i}"} for i in range(20)]))
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.source_count_warning is False
    assert result.source_id == "src-abc"


def test_source_count_at_warning_threshold_returns_warning_flag(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_list=_completed(stdout=json.dumps([{"id": f"src-{i}"} for i in range(45)]))
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    # Warning fires at >=45 but call still succeeds.
    assert result.source_count_warning is True
    assert result.source_id == "src-abc"
    assert result.failed is False


def test_source_count_exhausted_returns_failed_no_queue(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_list=_completed(stdout=json.dumps([{"id": f"src-{i}"} for i in range(50)]))
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is False
    assert "source count" in result.reason.lower() or "exhausted" in result.reason.lower()
    # source add should not be attempted when count is exhausted.
    cmds = [c.args[0] for c in run.call_args_list]
    assert not any("add" in cmd for cmd in cmds)
    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert not queue_dir.exists() or not list(queue_dir.glob("*.json"))


def test_source_list_supports_wrapper_response_shape(tmp_path):
    """Defensive parser: handle {'sources': [...]} shape as well as bare list."""
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_list=_completed(
            stdout=json.dumps({"sources": [{"id": f"src-{i}"} for i in range(10)]})
        )
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.source_count_warning is False
    assert result.source_id == "src-abc"


# ---------------------------------------------------------------------------
# Round 5: failure paths (no auth issue, no queueing)
# ---------------------------------------------------------------------------


def test_subprocess_timeout_returns_failed_no_queue(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=subprocess.TimeoutExpired(cmd="notebooklm", timeout=30),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is False
    assert "timeout" in result.reason.lower()
    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert not queue_dir.exists() or not list(queue_dir.glob("*.json"))


def test_json_parse_error_returns_failed_no_queue(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=_completed(stdout="not valid json", returncode=0),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is False
    assert "json" in result.reason.lower() or "parse" in result.reason.lower()


def test_runtime_unauthorized_pattern_writes_to_queue(tmp_path):
    """If precheck passed but the actual add returns auth-error mid-call,
    treat as transient auth failure and queue."""
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=_completed(returncode=1, stderr="Error: Unauthorized\n"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is True

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert queue_dir.is_dir()
    files = list(queue_dir.glob("*.json"))
    assert len(files) == 1


def test_runtime_csrf_error_pattern_writes_to_queue(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=_completed(returncode=1, stderr="CSRF token missing\n"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is True


def test_runtime_non_auth_error_returns_failed_no_queue(tmp_path):
    """Generic CLI failures that aren't auth-related should not queue."""
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=_completed(returncode=2, stderr="Error: invalid mime type\n"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert result.queued is False


def test_unexpected_exception_in_subprocess_returns_failed_not_raises(tmp_path):
    """The function must never raise; any uncaught error is wrapped as failed."""
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        source_add=RuntimeError("disk on fire"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.failed is True
    assert "disk on fire" in result.reason or "unexpected" in result.reason.lower()


# ---------------------------------------------------------------------------
# Round 6: queue mechanics (versioning, dedup)
# ---------------------------------------------------------------------------


def test_queue_file_has_versioned_schema(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        auth_check=_completed(returncode=1, stderr="Unauthorized"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    files = list(queue_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["note_path"] == str(note)
    assert payload["notebook_id"] == "nb-ops-id"
    assert payload["classification_primary"] == "ops"
    assert payload["retry_count"] == 0
    assert "queued_at" in payload


def test_queue_dedup_increments_retry_count_for_same_note_and_notebook(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)

    fake = _route_subprocess(
        auth_check=_completed(returncode=1, stderr="Unauthorized"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    files = list(queue_dir.glob("*.json"))
    # Same (notebook_id, note_path) deduplicates to a single file.
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["retry_count"] == 1


def test_queue_keeps_separate_files_for_different_notes(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note_a = _make_note(tmp_path, name="a.md")
    note_b = _make_note(tmp_path, name="b.md")

    fake = _route_subprocess(
        auth_check=_completed(returncode=1, stderr="Unauthorized"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note_a,
        )
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note_b,
        )

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    files = list(queue_dir.glob("*.json"))
    assert len(files) == 2


# ---------------------------------------------------------------------------
# Round 7: mode-agnostic
# ---------------------------------------------------------------------------


def test_emergent_mode_uses_classification_primary_as_lookup_key(tmp_path):
    """In emergent mode the lookup key is still classification.primary
    (the theme rather than the domain). Same code path."""
    config = _make_config(
        mode="emergent",
        vault_path=tmp_path,
        notebook_map={"branding-research": "nb-branding-id"},
    )
    config = replace(config, domains=())
    note = _make_note(tmp_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = integrate_notebooklm(
            classification=_make_classification(primary="branding-research", mode="emergent"),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.skipped is False
    assert result.failed is False
    assert result.source_id == "src-abc"
    assert result.notebook_id == "nb-branding-id"


def test_emergent_mode_with_no_mapping_skips_just_like_fixed_domains(tmp_path):
    config = _make_config(
        mode="emergent",
        vault_path=tmp_path,
        notebook_map={},
    )
    config = replace(config, domains=())
    note = _make_note(tmp_path)

    with patch("vault_intake.notebooklm.subprocess.run") as run:
        result = integrate_notebooklm(
            classification=_make_classification(primary="any-theme", mode="emergent"),
            frontmatter=_make_frontmatter(),
            config=config,
            note_path=note,
        )

    assert result.skipped is True
    run.assert_not_called()


# ---------------------------------------------------------------------------
# Round 8: flush_nlm_queue
# ---------------------------------------------------------------------------


def _seed_queue(
    vault_path: Path,
    *,
    note_path: Path,
    notebook_id: str = "nb-ops-id",
    classification_primary: str = "ops",
    retry_count: int = 0,
) -> Path:
    queue_dir = vault_path / ".vault-intake" / "nlm_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "queued_at": "2026-04-30T10:00:00Z",
        "note_path": str(note_path),
        "notebook_id": notebook_id,
        "classification_primary": classification_primary,
        "retry_count": retry_count,
    }
    # Deterministic filename matches the dedup key the writer uses.
    import hashlib
    digest = hashlib.sha1(
        f"{notebook_id}|{note_path}".encode("utf-8")
    ).hexdigest()
    queue_file = queue_dir / f"{digest}.json"
    queue_file.write_text(json.dumps(payload), encoding="utf-8")
    return queue_file


def test_flush_empty_queue_returns_zero_counts(tmp_path):
    config = _make_config(vault_path=tmp_path)

    with patch("vault_intake.notebooklm.subprocess.run") as run:
        result = flush_nlm_queue(config)

    assert isinstance(result, FlushResult)
    assert result.processed == 0
    assert result.still_queued == 0
    assert result.dropped == 0
    run.assert_not_called()


def test_flush_runs_auth_check_once_upfront(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note_a = _make_note(tmp_path, name="a.md")
    note_b = _make_note(tmp_path, name="b.md")
    _seed_queue(tmp_path, note_path=note_a)
    _seed_queue(tmp_path, note_path=note_b)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        flush_nlm_queue(config)

    auth_calls = [c for c in run.call_args_list if "auth check" in " ".join(c.args[0])]
    assert len(auth_calls) == 1


def test_flush_skips_drain_when_auth_check_fails(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)
    _seed_queue(tmp_path, note_path=note)

    fake = _route_subprocess(
        auth_check=_completed(returncode=1, stderr="Unauthorized"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        result = flush_nlm_queue(config)

    assert result.processed == 0
    assert result.still_queued == 1
    assert result.dropped == 0

    cmds = [c.args[0] for c in run.call_args_list]
    assert not any("source" in cmd for cmd in cmds)


def test_flush_drains_all_when_auth_fresh_and_adds_succeed(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note_a = _make_note(tmp_path, name="a.md")
    note_b = _make_note(tmp_path, name="b.md")
    _seed_queue(tmp_path, note_path=note_a)
    _seed_queue(tmp_path, note_path=note_b)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = flush_nlm_queue(config)

    assert result.processed == 2
    assert result.still_queued == 0
    assert result.dropped == 0

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert not list(queue_dir.glob("*.json"))


def test_flush_partial_when_some_adds_fail(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note_a = _make_note(tmp_path, name="a.md")
    note_b = _make_note(tmp_path, name="b.md")
    _seed_queue(tmp_path, note_path=note_a)
    _seed_queue(tmp_path, note_path=note_b)

    add_calls = {"count": 0}

    def fake_run(cmd, *args, **kwargs):
        joined = " ".join(cmd)
        if "auth check" in joined:
            return _completed(stdout="ok\n")
        if "source list" in joined:
            return _completed(stdout=json.dumps([]))
        if "source add" in joined:
            add_calls["count"] += 1
            if add_calls["count"] == 1:
                return _completed(stdout=json.dumps({"id": "src-1"}))
            return _completed(returncode=1, stderr="Error: Unauthorized\n")
        raise AssertionError(f"unexpected cmd: {cmd!r}")

    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake_run):
        result = flush_nlm_queue(config)

    assert result.processed == 1
    assert result.still_queued == 1
    assert result.dropped == 0


def test_flush_increments_retry_count_on_failure(tmp_path):
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)
    _seed_queue(tmp_path, note_path=note, retry_count=2)

    fake = _route_subprocess(
        source_add=_completed(returncode=1, stderr="Error: Unauthorized\n"),
    )
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        flush_nlm_queue(config)

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    files = list(queue_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["retry_count"] == 3


def test_flush_drops_items_where_note_file_no_longer_exists(tmp_path):
    config = _make_config(vault_path=tmp_path)
    deleted_path = tmp_path / "deleted.md"
    # Seed a queue entry whose underlying file is gone.
    _seed_queue(tmp_path, note_path=deleted_path)

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake) as run:
        result = flush_nlm_queue(config)

    assert result.dropped == 1
    assert result.processed == 0
    assert result.still_queued == 0

    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    assert not list(queue_dir.glob("*.json"))

    # No source add should be attempted for a missing note file.
    cmds = [c.args[0] for c in run.call_args_list]
    assert not any("source add" in " ".join(cmd) for cmd in cmds)


def test_flush_handles_corrupt_queue_file_as_dropped(tmp_path):
    config = _make_config(vault_path=tmp_path)
    queue_dir = tmp_path / ".vault-intake" / "nlm_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "garbage.json").write_text("not valid json", encoding="utf-8")

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        result = flush_nlm_queue(config)

    assert result.dropped == 1
    assert result.processed == 0
    assert result.still_queued == 0


# ---------------------------------------------------------------------------
# Round 9: result shape and frontmatter coupling contract
# ---------------------------------------------------------------------------


def test_notebooklm_result_is_frozen_dataclass():
    result = NotebookLMResult(
        source_id=None,
        notebook_id=None,
        skipped=True,
        failed=False,
        queued=False,
        reason="x",
        source_count_warning=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.source_id = "y"  # type: ignore[misc]


def test_flush_result_is_frozen_dataclass():
    result = FlushResult(processed=0, still_queued=0, dropped=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.processed = 1  # type: ignore[misc]


def test_integrate_does_not_mutate_input_frontmatter(tmp_path):
    """The function returns a result; the orchestrator handles
    frontmatter updates. Step 9 must not mutate its inputs."""
    config = _make_config(vault_path=tmp_path)
    note = _make_note(tmp_path)
    fm = _make_frontmatter()

    fake = _route_subprocess()
    with patch("vault_intake.notebooklm.subprocess.run", side_effect=fake):
        integrate_notebooklm(
            classification=_make_classification(),
            frontmatter=fm,
            config=config,
            note_path=note,
        )

    assert fm.source_id == ""  # unchanged
