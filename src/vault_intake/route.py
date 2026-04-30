"""Step 8: route to destination folder (mode-dependent).

Returns a path-suggestion plus audit metadata. The skill orchestrator
handles the actual file write at session-end confirmation; `route()`
itself is pure and has no filesystem side effects (no folder creation,
no file writes).

fixed_domains/para mode follows the spec's (type, PARA) destination
table (build spec lines 190-201). emergent mode looks up
`classification.primary` (the theme) against existing vault folders,
falling back to `_inbox/` when no folder matches (spec lines 205-210).

Function-side gate is unconditional. The skill orchestrator picks
whether to invoke. In fixed_domains mode `para` is required; in
emergent mode `para` is ignored.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .classify import ClassificationResult
from .config import Config, Mode
from .detect import DetectionResult
from .frontmatter import Frontmatter, NoteType
from .para import ParaCategory, ParaResult


@dataclass(frozen=True)
class RouteResult:
    destination: Path
    project_link_target: Path | None
    archive_flagged: bool
    inbox_fallback: bool
    is_section_update: bool
    reason: str
    mode: Mode


# Insight, workflow, prompt are user-set frontmatter types: the
# orchestrator confirms them at Step 5 (they are not derivable from
# detection alone). Routing for these is PARA-independent per spec
# lines 194-196.
_USER_SET_FOLDERS: dict[NoteType, str] = {
    "insight": "insights",
    "workflow": "workflows",
    "prompt": "prompts",
}

# (frontmatter.type, para.category) -> destination folder, for
# detection-derived types that depend on PARA. Spec lines 190-201.
_SPEC_TABLE: dict[tuple[NoteType, ParaCategory], str] = {
    ("session", "area"): "sessions",
    ("context", "area"): "context",
    ("reference", "resource"): "references",
    ("note", "area"): "sessions",
}

# Would-be destination when PARA=archive: route as if the type were the
# canonical default for that frontmatter type. Used to populate
# `destination` so the orchestrator can offer "route here, or move to
# archive/".
_ARCHIVE_PROXY_FOLDERS: dict[NoteType, str] = {
    "session": "sessions",
    "insight": "insights",
    "workflow": "workflows",
    "prompt": "prompts",
    "context": "context",
    "reference": "references",
    "note": "sessions",
}

_INBOX_NAME = "_inbox"


def route(
    detection: DetectionResult,
    classification: ClassificationResult,
    para: ParaResult | None,
    frontmatter: Frontmatter,
    config: Config,
) -> RouteResult:
    if config.mode == "emergent":
        return _route_emergent(classification, config)
    if para is None:
        raise ValueError(
            "fixed_domains route requires a ParaResult; got None"
        )
    return _route_fixed_domains(detection, para, frontmatter, config)


def _route_fixed_domains(
    detection: DetectionResult,
    para: ParaResult,
    frontmatter: Frontmatter,
    config: Config,
) -> RouteResult:
    archive_flagged = para.category == "archive"
    destination, link_target, is_section_update, reason = _resolve_fixed_domains_destination(
        detection, para, frontmatter, config.vault_path
    )
    inbox_fallback = destination == config.vault_path / _INBOX_NAME

    if archive_flagged:
        reason = f"{reason}; archive flagged"

    return RouteResult(
        destination=destination,
        project_link_target=link_target,
        archive_flagged=archive_flagged,
        inbox_fallback=inbox_fallback,
        is_section_update=is_section_update,
        reason=reason,
        mode="fixed_domains",
    )


def _resolve_fixed_domains_destination(
    detection: DetectionResult,
    para: ParaResult,
    frontmatter: Frontmatter,
    vault_path: Path,
) -> tuple[Path, Path | None, bool, str]:
    f_type = frontmatter.type
    d_type = detection.type
    p_cat = para.category

    # Routing key: use frontmatter.type when it carries semantic info, but
    # recover detection.type when Step 5's PARA-project override
    # collapsed the original session/context/note/prompt distinction to
    # frontmatter.type="project". This keeps prompt+project routing to
    # prompts/ (spec line 196 "prompt | any | prompts/") rather than
    # mis-routing to sessions/+link.
    effective_type = d_type if f_type == "project" else f_type

    # Insight/workflow/prompt route by type, PARA-independent.
    if effective_type in _USER_SET_FOLDERS:
        folder = _USER_SET_FOLDERS[effective_type]
        return (
            vault_path / folder,
            None,
            False,
            f"type={effective_type}, dest={folder}/",
        )

    # PARA=archive: route to canonical default folder; outer caller
    # sets archive_flagged.
    if p_cat == "archive":
        folder = _ARCHIVE_PROXY_FOLDERS.get(effective_type, _INBOX_NAME)
        return (
            vault_path / folder,
            None,
            False,
            f"type={effective_type}, para=archive, would-be={folder}/",
        )

    # PARA-project routing: use effective_type to disambiguate
    # context-vs-session-vs-note. Other types (reference, document,
    # transcription) with project PARA are unlisted in the spec; fall
    # back to _inbox/.
    if p_cat == "project":
        slug = para.project_slug or frontmatter.project
        if not slug:
            return (
                vault_path / _INBOX_NAME,
                None,
                False,
                f"type={effective_type} para=project missing project_slug, dest={_INBOX_NAME}/",
            )
        project_file = vault_path / "projects" / f"{slug}.md"
        if effective_type == "context":
            return (
                project_file,
                project_file,
                True,
                f"type=context, para=project, dest=projects/{slug}.md (section update)",
            )
        if effective_type in {"session", "note"}:
            return (
                vault_path / "sessions",
                project_file,
                False,
                f"type={effective_type}, para=project, dest=sessions/ + link",
            )
        return (
            vault_path / _INBOX_NAME,
            None,
            False,
            f"unlisted combo type={effective_type}, para=project, dest={_INBOX_NAME}/",
        )

    # Spec table rows for session/context/reference/note with non-project
    # PARA categories.
    table_dest = _SPEC_TABLE.get((effective_type, p_cat))
    if table_dest is not None:
        return (
            vault_path / table_dest,
            None,
            False,
            f"type={effective_type}, para={p_cat}, dest={table_dest}/",
        )

    return (
        vault_path / _INBOX_NAME,
        None,
        False,
        f"unlisted combo type={effective_type}, para={p_cat}, dest={_INBOX_NAME}/",
    )


def _route_emergent(
    classification: ClassificationResult,
    config: Config,
) -> RouteResult:
    theme = classification.primary
    matched_folder = _find_emergent_folder(theme, config.vault_path)

    if matched_folder is not None:
        return RouteResult(
            destination=matched_folder,
            project_link_target=None,
            archive_flagged=False,
            inbox_fallback=False,
            is_section_update=False,
            reason=f"emergent: theme={theme}, dest={matched_folder.name}/",
            mode="emergent",
        )

    return RouteResult(
        destination=config.vault_path / _INBOX_NAME,
        project_link_target=None,
        archive_flagged=False,
        inbox_fallback=True,
        is_section_update=False,
        reason=f"emergent: theme={theme} has no matching folder, dest={_INBOX_NAME}/",
        mode="emergent",
    )


def _find_emergent_folder(theme: str, vault_path: Path) -> Path | None:
    """Return the vault folder matching `theme`, or None.

    Matches exact theme name and slugified variant. Skips
    underscore-prefixed system folders (`_inbox`, `_sinteses`, etc.) so
    a theme literally named "inbox" cannot collide with the system
    inbox.
    """
    if not vault_path.is_dir():
        return None

    target_slug = _kebab(theme)
    candidates = {theme, target_slug} - {""}
    for entry in vault_path.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue
        if entry.name in candidates or _kebab(entry.name) == target_slug:
            return entry
    return None


def _kebab(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
