# Changelog

All notable changes to vault-intake are documented here.

## [0.2.1] - 2026-05-03

Documentation patch: clarifies vault management surface ahead of M3 scoping.

### Added

- README sections covering: where files live (path table), running multiple vaults, the multi-account NotebookLM constraint (single-account-at-a-time per upstream CLI), and how to move a vault.
- README header bumped to reflect M2 (v0.2.0) rather than M1.

## [0.2.0] - 2026-05-02

M2 emergent-mode sprint: ships the second classification mode end-to-end, plus the `--inbox` batch flag for Wispr-Flow-style watch-folder workflows.

### Added

- **Emergent-mode classification (Step 3)**: `_classify_emergent` reads theme candidates from top-level vault folders and markdown `theme` frontmatter, then scores input by word frequency. Single-word themes break ties by occurrence count instead of set intersection. System folders (`_*`, `.*`) are excluded; new themes are proposed as the most-frequent significant token when no candidates match.
- **Emergent-mode frontmatter (Step 5)**: `Frontmatter` dataclass gains a `theme` field; `to_yaml()` emits a mode-conditional shape (`theme` for emergent, `domain` + `project` for fixed_domains). The `type` field is an open enum in emergent, closed in fixed_domains.
- **Emergent-mode wikilinks (Step 6)**: same-theme notes get weight 4 (replaces the cross-domain signal). Concept overlap (weight 2) and backlog markers (weight 1) work in both modes; project signal (weight 3) is fixed_domains only.
- **`--inbox` batch flag** for `scripts/intake.py`: scans `{vault}/inbox/` for `.md` files, runs the dry-run pass for each, prints a preview table, asks one batch confirmation, then writes all and moves source files to `{vault}/.vault-intake/inbox-processed/`. Mutually exclusive with `--input`. Per-file errors do not abort the batch; final summary reports written/skipped/failed counts. Archive collisions get a UTC timestamp suffix.
- **Compact braindump titles**: when classification is confident, braindump notes get `braindump-{domain|theme}-{date}` filenames (was: long sentence-derived slugs from M1.1). Falls back to the sentence heuristic when classification is uncertain. Now fires for `note`, `context`, and `prompt` braindumps (was: only `note`).

### Fixed

- Codex review B-2: `--inbox` batch confirmation prompt now counts processable runs only, not total `.md` files including read-failed ones.
- Codex review B-4: braindump compact-title gate widened to all three brain-dump-eligible content types.

## [0.1.1] - 2026-05-02

M1.1 patch: six fixes from Elio's day-1 dogfood feedback.

### Added

- `bootstrap_vault()` now creates `inbox/` alongside the `_inbox/` system fallback, and creates `<domain>/sessions/` per configured domain instead of a flat `sessions/` root.
- Content snippet (first 200 characters) surfaced before the classification confirmation question so users see context before approving.
- Route rationale line (`Route: ...`) added to pipeline summary so users know why a note was routed to its destination.
- Refinement diff shown in pipeline output when the refiner changes text; new `REFINEMENT_ACCEPT` confirmation question gates the write.
- pt-BR stopword filter extended; braindump notes now receive `braindump-<slug>-<date>` filenames instead of long sentence-derived slugs.
- Domain-scoped routing: sessions land in `<domain>/sessions/` rather than a flat `sessions/` folder.

### Fixed

- Bootstrap directory layout aligned with Elio's vault structure expectations.
- Codex review items R1, T1, T2, N1, N2 addressed in bootstrap implementation.

## [0.1.0] - 2026-04-30

Initial M1 release. Fixed-domains mode end-to-end: Steps 0-9 implemented, two orchestrator entrypoints (`run_intake` / `confirm_and_write`), CLI wrappers (`intake.py`, `flush_nlm.py`, `install_skill.py`), NotebookLM integration with auth precheck and persistent retry queue.
