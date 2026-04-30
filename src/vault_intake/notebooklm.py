"""Step 9: NotebookLM integration (opt-in, graceful degradation).

Per build spec lines 216-226: when `skip_notebooklm: false` and
`notebook_map` has an entry for the note's classification key
(`classification.primary`, identical key for both modes), look up the
notebook ID, check source count (warn if `>= 45/50`), add the note as a
source via the `notebooklm` CLI, and return the new source ID. On
failure, log and continue (never block).

Auth model and queue rationale (signed off 2026-04-30):

The `notebooklm` CLI auth has two layers. CSRF tokens / session IDs
expire on the order of minutes but the CLI auto-refreshes them on
auth-error and retries once. The underlying Google session cookies
expire "every few weeks" and require manual `notebooklm login`. The
self-refresh handles the short-lived layer; we only see failures when
the deep cookies are dead.

Two extensions over the bare spec:

1. `notebooklm auth check --test` runs as a precheck before any source
   add. Cheap, purpose-built, fails fast on dead cookies. Saves
   subprocess time on doomed adds.

2. When the precheck fails or the runtime add returns an auth-error
   pattern (`Unauthorized`, `redirect`, `CSRF token missing`), the
   pending action is serialized as JSON to
   `<vault>/.vault-intake/nlm_queue/<sha1>.json` (key:
   `notebook_id|note_path`). A separate `flush_nlm_queue()` library
   function drains the queue once the user runs `notebooklm login`.
   Non-auth failures (timeout, JSON parse error, source-count
   exhausted) are NOT queued because re-auth would not recover them.

Function-side behavior is unconditional and never raises. Mode-agnostic:
fixed_domains and emergent share the same code path; both look up
`classification.primary` in `config.notebook_map`.

CLI invocation contract:

- Auth check:   `notebooklm auth check --test`
- Source list:  `notebooklm source list -n <id> --json`
- Source add:   `notebooklm source add <path> -n <id> --json`

All calls run with `env={"PYTHONIOENCODING": "utf-8", ...}` per the
Windows gotcha that Rich emits Unicode that breaks the default Windows
codepage. `subprocess.run` runs `text=True`, `capture_output=True`,
`timeout=30`, `check=False`.

JSON parser is defensive: tries top-level `id` then `source_id`; for
list responses tries top-level list then `{"sources": [...]}` wrapper.
The CLI's exact response shape is not authoritatively documented for
file-uploaded sources, so the parser tolerates either shape.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .classify import ClassificationResult
from .config import Config
from .frontmatter import Frontmatter


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotebookLMResult:
    source_id: str | None
    notebook_id: str | None
    skipped: bool
    failed: bool
    queued: bool
    reason: str
    source_count_warning: bool


@dataclass(frozen=True)
class FlushResult:
    processed: int
    still_queued: int
    dropped: int


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_QUEUE_SUBPATH = (".vault-intake", "nlm_queue")
_QUEUE_SCHEMA_VERSION = 1
_SUBPROCESS_TIMEOUT = 30
_SOURCE_WARNING_THRESHOLD = 45  # NotebookLM Standard plan: 50 cap; warn early.
_SOURCE_LIMIT = 50

_AUTH_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bunauthorized\b", re.IGNORECASE),
    # Redirect-to-auth includes past tense ("redirected to login"); requires
    # nearby login/sign-in/auth context so non-auth "redirect URL invalid"
    # style errors do not falsely queue.
    re.compile(
        r"\bredirect(?:ed)?\s+to\s+(?:login|sign[-\s]?in|auth(?:entication)?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcsrf\s*token\s*(missing|expired)\b", re.IGNORECASE),
    re.compile(r"\bSNlM0e\s*not\s*found\b", re.IGNORECASE),
    re.compile(r"\bauth(?:entication)?\s*(failed|expired|required)\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def integrate_notebooklm(
    classification: ClassificationResult,
    frontmatter: Frontmatter,
    config: Config,
    *,
    note_path: Path | None = None,
    nlm_command: str = "notebooklm",
) -> NotebookLMResult:
    """Add the assembled note to NotebookLM, gracefully degrading on failure.

    Returns a NotebookLMResult describing the outcome. Never raises,
    never mutates inputs. The orchestrator is responsible for updating
    `frontmatter.source_id` (via `dataclasses.replace`) when the result
    carries a non-None `source_id`.
    """
    del frontmatter  # accepted for signature parity; orchestrator owns updates.

    if config.skip_notebooklm:
        return _skipped("skip_notebooklm=True", notebook_id=None)

    notebook_id = config.notebook_map.get(classification.primary)
    if not notebook_id:
        return _skipped(
            f"no mapping for classification key '{classification.primary}'",
            notebook_id=None,
        )

    if note_path is None:
        return _skipped("dry-run: no note_path provided", notebook_id=notebook_id)

    # Auth precheck: fast-fail on dead cookies before any add attempt.
    try:
        auth_ok, auth_reason = _auth_check(nlm_command)
    except FileNotFoundError:
        return _skipped("notebooklm CLI not available on PATH", notebook_id=notebook_id)
    except Exception as exc:  # noqa: BLE001 - never-block contract
        return _failed(
            f"unexpected error during auth check: {exc}",
            notebook_id=notebook_id,
        )

    if not auth_ok:
        queued = _try_queue(
            config=config,
            note_path=note_path,
            notebook_id=notebook_id,
            classification_primary=classification.primary,
        )
        return NotebookLMResult(
            source_id=None,
            notebook_id=notebook_id,
            skipped=False,
            failed=True,
            queued=queued,
            reason=f"auth precheck failed: {auth_reason}",
            source_count_warning=False,
        )

    # Source-count check.
    try:
        count = _source_count(notebook_id, nlm_command)
    except FileNotFoundError:
        return _skipped("notebooklm CLI not available on PATH", notebook_id=notebook_id)
    except subprocess.TimeoutExpired:
        return _failed("source list timeout", notebook_id=notebook_id)
    except _CLIError as exc:
        if _is_auth_error(exc.message):
            queued = _try_queue(
                config=config,
                note_path=note_path,
                notebook_id=notebook_id,
                classification_primary=classification.primary,
            )
            return NotebookLMResult(
                source_id=None,
                notebook_id=notebook_id,
                skipped=False,
                failed=True,
                queued=queued,
                reason=f"source list auth error: {exc.message}",
                source_count_warning=False,
            )
        return _failed(f"source list failed: {exc.message}", notebook_id=notebook_id)
    except Exception as exc:  # noqa: BLE001
        return _failed(
            f"unexpected error during source list: {exc}",
            notebook_id=notebook_id,
        )

    if count >= _SOURCE_LIMIT:
        return _failed(
            f"source count exhausted: {count}/{_SOURCE_LIMIT}",
            notebook_id=notebook_id,
        )

    warn = count >= _SOURCE_WARNING_THRESHOLD

    # Source add.
    try:
        source_id = _source_add(notebook_id, note_path, nlm_command)
    except FileNotFoundError:
        return _skipped("notebooklm CLI not available on PATH", notebook_id=notebook_id)
    except subprocess.TimeoutExpired:
        return _failed("source add timeout", notebook_id=notebook_id)
    except _CLIError as exc:
        if _is_auth_error(exc.message):
            queued = _try_queue(
                config=config,
                note_path=note_path,
                notebook_id=notebook_id,
                classification_primary=classification.primary,
            )
            return NotebookLMResult(
                source_id=None,
                notebook_id=notebook_id,
                skipped=False,
                failed=True,
                queued=queued,
                reason=f"source add auth error: {exc.message}",
                source_count_warning=warn,
            )
        return _failed(
            f"source add failed: {exc.message}",
            notebook_id=notebook_id,
            source_count_warning=warn,
        )
    except json.JSONDecodeError as exc:
        return _failed(
            f"source add JSON parse error: {exc.msg}",
            notebook_id=notebook_id,
            source_count_warning=warn,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(
            f"unexpected error during source add: {exc}",
            notebook_id=notebook_id,
            source_count_warning=warn,
        )

    return NotebookLMResult(
        source_id=source_id,
        notebook_id=notebook_id,
        skipped=False,
        failed=False,
        queued=False,
        reason=f"added to {notebook_id}",
        source_count_warning=warn,
    )


def flush_nlm_queue(
    config: Config,
    *,
    nlm_command: str = "notebooklm",
) -> FlushResult:
    """Drain the persisted retry queue once the user has re-authenticated.

    Returns processed/still_queued/dropped counts. Runs `auth check`
    once upfront; if auth is dead, returns immediately with all items
    counted as still_queued. Never raises.
    """
    queue_dir = _queue_dir(config)
    if not queue_dir.is_dir():
        return FlushResult(processed=0, still_queued=0, dropped=0)

    queue_files = sorted(queue_dir.glob("*.json"))
    if not queue_files:
        return FlushResult(processed=0, still_queued=0, dropped=0)

    # Pre-load and partition: drop corrupt files and missing notes
    # before any subprocess work.
    valid: list[tuple[Path, dict]] = []
    dropped = 0
    for queue_file in queue_files:
        payload = _read_queue_file(queue_file)
        if payload is None:
            _safe_unlink(queue_file)
            dropped += 1
            continue
        note_path_str = payload.get("note_path")
        if not note_path_str or not Path(note_path_str).exists():
            _safe_unlink(queue_file)
            dropped += 1
            continue
        valid.append((queue_file, payload))

    if not valid:
        return FlushResult(processed=0, still_queued=0, dropped=dropped)

    # Auth precheck before any drain attempt.
    try:
        auth_ok, _ = _auth_check(nlm_command)
    except FileNotFoundError:
        return FlushResult(processed=0, still_queued=len(valid), dropped=dropped)
    except Exception:  # noqa: BLE001
        return FlushResult(processed=0, still_queued=len(valid), dropped=dropped)

    if not auth_ok:
        return FlushResult(processed=0, still_queued=len(valid), dropped=dropped)

    processed = 0
    still_queued = 0
    for queue_file, payload in valid:
        # _read_queue_file already validated these fields are non-empty
        # strings and that retry_count is normalized.
        notebook_id = payload["notebook_id"]
        note_path = Path(payload["note_path"])
        try:
            _ = _source_count(notebook_id, nlm_command)
        except Exception:  # noqa: BLE001 - count is advisory during drain
            pass
        try:
            _source_add(notebook_id, note_path, nlm_command)
        except Exception:  # noqa: BLE001 - any add failure leaves the entry queued
            payload["retry_count"] = _safe_int(payload.get("retry_count")) + 1
            # If the rewrite fails (disk full, permissions, etc.) the
            # original file remains untouched on disk because the write
            # is atomic via temp + os.replace; still count as queued so
            # the count surfaces accurately.
            _write_queue_payload(queue_file, payload)
            still_queued += 1
            continue
        _safe_unlink(queue_file)
        processed += 1

    return FlushResult(
        processed=processed,
        still_queued=still_queued,
        dropped=dropped,
    )


# ---------------------------------------------------------------------------
# Internal: subprocess invocation
# ---------------------------------------------------------------------------


class _CLIError(Exception):
    """Raised on nonzero CLI exit; carries the merged stderr/stdout text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        env=env,
    )


def _auth_check(nlm_command: str) -> tuple[bool, str]:
    proc = _run([nlm_command, "auth", "check", "--test"])
    if proc.returncode == 0:
        return True, ""
    merged = f"{proc.stderr or ''}\n{proc.stdout or ''}".strip()
    return False, merged or f"exit {proc.returncode}"


def _source_count(notebook_id: str, nlm_command: str) -> int:
    proc = _run([nlm_command, "source", "list", "-n", notebook_id, "--json"])
    if proc.returncode != 0:
        merged = f"{proc.stderr or ''}\n{proc.stdout or ''}".strip()
        raise _CLIError(merged or f"exit {proc.returncode}")
    sources = _parse_source_list(proc.stdout or "")
    return len(sources)


def _source_add(notebook_id: str, note_path: Path, nlm_command: str) -> str:
    proc = _run(
        [
            nlm_command,
            "source",
            "add",
            str(note_path),
            "-n",
            notebook_id,
            "--json",
        ]
    )
    if proc.returncode != 0:
        merged = f"{proc.stderr or ''}\n{proc.stdout or ''}".strip()
        raise _CLIError(merged or f"exit {proc.returncode}")
    return _parse_source_id(proc.stdout or "")


# ---------------------------------------------------------------------------
# Internal: JSON parsing (defensive)
# ---------------------------------------------------------------------------


def _parse_source_list(stdout: str) -> list:
    """Return the source entries for counting purposes.

    Returns the raw items as the CLI emitted them; callers must only
    count `len(...)` rather than introspect fields, because the CLI's
    documented-but-not-fully-stable JSON shape may emit either dict
    items (typical) or bare ID strings, and dropping non-dict items
    would silently undercount and cause a 50/50 cap miss.
    """
    parsed = json.loads(stdout)
    if isinstance(parsed, list):
        return list(parsed)
    if isinstance(parsed, dict) and isinstance(parsed.get("sources"), list):
        return list(parsed["sources"])
    return []


def _parse_source_id(stdout: str) -> str:
    parsed = json.loads(stdout)
    if isinstance(parsed, dict):
        for key in ("id", "source_id"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
        # Some shapes wrap under "source": {...}.
        nested = parsed.get("source")
        if isinstance(nested, dict):
            for key in ("id", "source_id"):
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return value
    raise json.JSONDecodeError("source_id field not found in CLI output", stdout, 0)


def _is_auth_error(message: str) -> bool:
    return any(p.search(message) for p in _AUTH_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Internal: queue read/write
# ---------------------------------------------------------------------------


def _queue_dir(config: Config) -> Path:
    return config.vault_path.joinpath(*_QUEUE_SUBPATH)


def _queue_filename(notebook_id: str, note_path: Path) -> str:
    key = f"{notebook_id}|{note_path}".encode("utf-8")
    return hashlib.sha1(key).hexdigest() + ".json"


def _try_queue(
    *,
    config: Config,
    note_path: Path,
    notebook_id: str,
    classification_primary: str,
) -> bool:
    """Write or update a queue entry. Returns True on success.

    Dedup key is `(notebook_id, note_path)`. If a queue file already
    exists at the deterministic key, increment its retry_count rather
    than creating a duplicate. Never raises; on any error returns False.
    """
    try:
        queue_dir = _queue_dir(config)
        queue_dir.mkdir(parents=True, exist_ok=True)
        queue_file = queue_dir / _queue_filename(notebook_id, note_path)
        existing = _read_queue_file(queue_file) if queue_file.exists() else None
        if existing is not None:
            existing["retry_count"] = _safe_int(existing.get("retry_count")) + 1
            return _write_queue_payload(queue_file, existing)
        payload = {
            "schema_version": _QUEUE_SCHEMA_VERSION,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "note_path": str(note_path),
            "notebook_id": notebook_id,
            "classification_primary": classification_primary,
            "retry_count": 0,
        }
        return _write_queue_payload(queue_file, payload)
    except Exception:  # noqa: BLE001 - never-block contract
        return False


def _read_queue_file(queue_file: Path) -> dict | None:
    """Return a validated payload dict, or None if invalid/corrupt.

    Validation: the file must be readable, parse as a JSON object, carry
    the current schema_version, and contain non-empty `notebook_id` and
    `note_path` strings. Missing or malformed required fields make the
    entry invalid; flush_nlm_queue counts these as `dropped`.
    """
    try:
        text = queue_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema_version") != _QUEUE_SCHEMA_VERSION:
        return None
    notebook_id = parsed.get("notebook_id")
    note_path = parsed.get("note_path")
    if not isinstance(notebook_id, str) or not notebook_id:
        return None
    if not isinstance(note_path, str) or not note_path:
        return None
    # Normalize retry_count so downstream arithmetic never raises.
    parsed["retry_count"] = _safe_int(parsed.get("retry_count"))
    return parsed


def _write_queue_payload(queue_file: Path, payload: dict) -> bool:
    """Atomic queue write via temp file + os.replace.

    Returns True on success, False on any IO/serialization error. Never
    raises so callers can use the boolean to decide whether to count
    the entry as queued or as a write-failure.
    """
    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return False
    tmp = queue_file.with_suffix(queue_file.suffix + ".tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        os.replace(tmp, queue_file)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _safe_int(value: object, default: int = 0) -> int:
    """Coerce `value` to int, returning `default` on any failure.

    bool is rejected because the semantic intent is a counter, and
    Python's `bool` is a subclass of `int` that would otherwise be
    accepted silently. Non-finite floats (inf, -inf, NaN) and floats
    beyond the int range raise on `int(...)`; they are caught and
    return `default` so a corrupt queue entry never crashes the flush.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return default
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


# ---------------------------------------------------------------------------
# Internal: result builders
# ---------------------------------------------------------------------------


def _skipped(reason: str, *, notebook_id: str | None) -> NotebookLMResult:
    return NotebookLMResult(
        source_id=None,
        notebook_id=notebook_id,
        skipped=True,
        failed=False,
        queued=False,
        reason=reason,
        source_count_warning=False,
    )


def _failed(
    reason: str,
    *,
    notebook_id: str | None,
    source_count_warning: bool = False,
) -> NotebookLMResult:
    return NotebookLMResult(
        source_id=None,
        notebook_id=notebook_id,
        skipped=False,
        failed=True,
        queued=False,
        reason=reason,
        source_count_warning=source_count_warning,
    )


__all__ = [
    "FlushResult",
    "NotebookLMResult",
    "flush_nlm_queue",
    "integrate_notebooklm",
]
