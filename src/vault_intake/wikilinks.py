"""Step 6: generate wikilinks (mode-aware).

Produces ranked wikilink proposals for the new note's "Related" section
by walking the vault, parsing each markdown file's frontmatter, and
scoring candidates against four signals (build spec lines 163-167):

1. Cross-domain links (weight 4): existing notes whose `domain` field is
   in the new note's `classification.secondary` set. Highest-signal in
   fixed_domains mode because cross-domain connections expose PARA's
   most valuable links.
2. Active project links (weight 3): when `para.category == "project"`,
   propose a link to the project hub (`projects/{slug}.md` or
   `projects/{slug}/`). Always emitted in project category regardless
   of vault contents.
3. Concept overlap (weight 2): existing notes whose title shares at
   least two significant tokens with the new note body. Reuses the
   classify-side tokenizer and stop-word list so domain-token semantics
   stay consistent with Step 3.
4. Empty backlog markers (weight 1): user-typed `[[X]]` literals in the
   body that do not match any existing vault note's frontmatter title
   or filename stem. Honors the user's explicit intent without auto-
   generating markers from arbitrary nouns.

When a single target qualifies under multiple signals, the highest
weight wins (dedupe by target). Ties break by recency (newer mtime
first), then alphabetical by source path. Output is capped at
`max_proposals` (default 7); fewer than `min_proposals_target`
candidates returns what we have rather than padding with low-quality
matches.

Function-side gate is unconditional. The skill orchestrator decides
whether to invoke `generate_wikilinks()` based on `config.mode`.
Emergent mode raises `NotImplementedError` until the parallel track
ships.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import yaml

from .classify import ClassificationResult, _tokenize
from .config import Config
from .para import ParaResult


Mode = Literal["fixed_domains", "emergent"]


# Minimum significant tokens shared between an existing note's title
# and the new note body before concept-overlap fires (build spec line
# 166: "concept overlap by title match"; v1 uses a 2-token floor to
# suppress single-token noise).
_CONCEPT_OVERLAP_FLOOR = 2

# Default cap on returned proposals (build spec line 169:
# "Do not exceed 7 to avoid noise.").
_DEFAULT_MAX_PROPOSALS = 7

# Default minimum target before declining to add weak fillers; the
# function returns what it has when fewer candidates exist (build spec
# line 169: "top 3-7 suggested wikilinks").
_DEFAULT_MIN_PROPOSALS = 3

# Folder names skipped during vault walk. `_indexes/` is excluded per
# the v1 source-strategy decision: the walk uses real notes' frontmatter
# rather than curated index files. Dot-prefixed directories (`.git`,
# `.obsidian`) are skipped wholesale.
_SKIP_DIRS = frozenset({"_indexes"})

# Captures `[[target]]` literal wikilinks in the body. Aliases like
# `[[target|label]]` are split so the target portion is matched against
# vault contents.
_TYPED_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


@dataclass(frozen=True)
class Wikilink:
    target: str
    weight: int
    source_path: Path | None
    reason: str


@dataclass(frozen=True)
class WikilinkResult:
    proposals: tuple[Wikilink, ...]
    mode: Mode
    candidates_considered: int


@dataclass(frozen=True)
class _VaultNote:
    path: Path
    label: str            # frontmatter title when set, else filename stem
    domain: str | None    # frontmatter `domain` value, when present
    title_tokens: frozenset[str]
    mtime: float


@dataclass(frozen=True)
class _Candidate:
    target: str
    weight: int
    source_path: Path | None
    reason: str
    mtime: float


def generate_wikilinks(
    text: str,
    classification: ClassificationResult,
    para: ParaResult,
    config: Config,
    *,
    max_proposals: int = _DEFAULT_MAX_PROPOSALS,
    min_proposals_target: int = _DEFAULT_MIN_PROPOSALS,
) -> WikilinkResult:
    if config.mode == "emergent":
        raise NotImplementedError(
            "emergent mode wikilinks are not implemented in v1; "
            "use classification_mode: fixed_domains for now"
        )

    # `min_proposals_target` is intentionally not enforced as a floor.
    # The contract per spec line 169 is "top 3-7"; when fewer candidates
    # exist we return what we have rather than padding with weak fillers.
    # The parameter is accepted so callers can document their target,
    # but the function never adds low-quality candidates to satisfy it.
    del min_proposals_target

    vault_notes = tuple(_walk_vault(config.vault_path))
    body_tokens = _tokenize(text)
    secondary_set = set(classification.secondary)
    existing_labels = _collect_existing_labels(vault_notes)

    candidates: dict[str, _Candidate] = {}

    for note in vault_notes:
        if note.domain is not None and note.domain in secondary_set:
            cross = _Candidate(
                target=note.label,
                weight=4,
                source_path=note.path,
                reason=f"cross-domain ({classification.primary}, {note.domain})",
                mtime=note.mtime,
            )
            _upsert(candidates, cross)

        if note.title_tokens:
            overlap = note.title_tokens & body_tokens
            if len(overlap) >= _CONCEPT_OVERLAP_FLOOR:
                shared = ", ".join(sorted(overlap)[:3])
                concept = _Candidate(
                    target=note.label,
                    weight=2,
                    source_path=note.path,
                    reason=f"concept overlap on {shared}",
                    mtime=note.mtime,
                )
                _upsert(candidates, concept)

    if para.category == "project" and para.project_slug:
        slug = para.project_slug
        source = _resolve_project_source(config.vault_path, slug)
        mtime = source.stat().st_mtime if source is not None else 0.0
        project = _Candidate(
            target=slug,
            weight=3,
            source_path=source,
            reason=f"active project: {slug}",
            mtime=mtime,
        )
        _upsert(candidates, project)

    seen_typed: set[str] = set()
    for raw_target in _scan_typed_wikilinks(text):
        normalized = raw_target.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen_typed:
            continue
        seen_typed.add(key)
        if key in existing_labels:
            continue
        marker = _Candidate(
            target=normalized,
            weight=1,
            source_path=None,
            reason="user-typed wikilink to uncreated note (backlog marker)",
            mtime=0.0,
        )
        _upsert(candidates, marker)

    candidates_considered = len(candidates)

    sorted_candidates = sorted(
        candidates.values(),
        key=_sort_key,
    )

    proposals = tuple(
        Wikilink(
            target=c.target,
            weight=c.weight,
            source_path=c.source_path,
            reason=c.reason,
        )
        for c in sorted_candidates[:max_proposals]
    )

    return WikilinkResult(
        proposals=proposals,
        mode="fixed_domains",
        candidates_considered=candidates_considered,
    )


def _sort_key(c: _Candidate) -> tuple[int, float, str]:
    # Weight desc (negate), recency desc (negate), then deterministic
    # tiebreak by source path string. Backlog markers (source_path is
    # None) fall to the back of their weight band by sorting on a
    # high-Unicode sentinel keyed by target.
    if c.source_path is None:
        path_key = "￿" + c.target
    else:
        path_key = str(c.source_path)
    return (-c.weight, -c.mtime, path_key)


def _upsert(candidates: dict[str, _Candidate], new: _Candidate) -> None:
    existing = candidates.get(new.target)
    if existing is None:
        candidates[new.target] = new
        return
    if new.weight > existing.weight:
        candidates[new.target] = new
        return
    if new.weight == existing.weight and new.mtime > existing.mtime:
        candidates[new.target] = new


def _walk_vault(vault_path: Path) -> Iterable[_VaultNote]:
    if not vault_path.is_dir():
        return
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for fname in files:
            if fname.startswith(".") or not fname.endswith(".md"):
                continue
            path = Path(root) / fname
            note = _read_note(path)
            if note is not None:
                yield note


def _read_note(path: Path) -> _VaultNote | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = _parse_frontmatter(text)
    title_value = fm.get("title")
    title = title_value.strip() if isinstance(title_value, str) and title_value.strip() else None
    label = title or path.stem
    domain_value = fm.get("domain")
    domain = domain_value.strip() if isinstance(domain_value, str) and domain_value.strip() else None
    title_tokens = frozenset(_tokenize(label))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _VaultNote(
        path=path,
        label=label,
        domain=domain,
        title_tokens=title_tokens,
        mtime=mtime,
    )


def _parse_frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---"):
        return {}
    after_open = text[3:].lstrip("\r\n")
    close_match = re.search(r"\n---\s*(?:\n|$)", "\n" + after_open)
    if close_match is None:
        return {}
    yaml_text = ("\n" + after_open)[: close_match.start()]
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _collect_existing_labels(notes: Iterable[_VaultNote]) -> set[str]:
    labels: set[str] = set()
    for note in notes:
        labels.add(note.label.lower())
        labels.add(note.path.stem.lower())
    return labels


def _scan_typed_wikilinks(text: str) -> Iterable[str]:
    for match in _TYPED_WIKILINK_RE.finditer(text):
        target = match.group(1)
        # Strip Obsidian-style `[[target|alias]]` syntax: keep the
        # target portion only so vault matching works on the canonical
        # name, not the displayed alias.
        if "|" in target:
            target = target.split("|", 1)[0]
        yield target


def _resolve_project_source(vault_path: Path, slug: str) -> Path | None:
    md_path = vault_path / "projects" / f"{slug}.md"
    if md_path.is_file():
        return md_path
    dir_path = vault_path / "projects" / slug
    if dir_path.is_dir():
        return dir_path
    return None


# Re-exported for callers that want the same tokenizer used internally
# (parallel to classify's exported helpers, even though the function
# itself is private-by-convention).
__all__ = [
    "Wikilink",
    "WikilinkResult",
    "generate_wikilinks",
]
