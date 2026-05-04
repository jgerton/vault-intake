# Changelog

All notable changes to vault-intake are documented here.

## [0.3.1] - 2026-05-04

Codex post-implementation review of v0.3.0 caught a P0 phantom-theme bug introduced by the new PARA-nested layout, plus pt-BR contractions that slipped past the v0.3.0 stopword filter. Patch ships P0 + P1 fixes with six new regression tests.

### Fixed

- **Areas-as-phantom-theme (P0).** v0.3.0's PARA-nested layout creates `<vault>/Areas/` for fixed_domains configs. In emergent mode, `_collect_emergent_themes` only excluded underscore- and dot-prefixed folders, so `Areas/` (and any other PARA convention dir) became a theme candidate. With no scoring evidence, `_classify_emergent` fell back to the alphabetically-first folder, promoting `Areas` as the primary theme and routing the note into the PARA dir. v0.3.1 adds `Areas`, `Projects`, `Resources`, `Archives` to a `_SKIP_SYSTEM_DIRS` set, and removes the zero-score fallback so an empty proposed theme stays empty (uncertain) instead of grabbing the alphabetically-first folder.
- **pt-BR contractions slipped past stopword filter.** v0.3.0 added `para` and `pra` but missed `pro`, `pros`, `pras`, `ai`, `aí`. `pras` is exactly 4 chars and slipped past `_MIN_THEME_WORD_LEN`, so a braindump with repeated `pras` proposed it as a theme. Added all five to the pt-BR stopword set.

### Added

- Six regression tests (Codex Bucket E):
  1. PARA dirs (`Areas`/`Projects`/`Resources`/`Archives`) never become theme candidates
  2. Word "areas" in input does not produce a confident `Areas` classification
  3. `language: pt` alias filters pt-BR stopwords identically to `language: pt-BR`
  4. Unknown `language: es` falls back to English stopwords without crashing
  5. pt-BR contractions (`pras`, `pros`, `pro`, `ai`, `aí`) are filtered from emergent themes
  6. Explicit `refinement_enabled: true` in CLAUDE.md round-trips to `Config.refinement_enabled is True` (pinning the contract claimed in the v0.3.0 changelog)

## [0.3.0] - 2026-05-04

Elio dogfood feedback round 2: PARA-nested layout, multi-language stopwords, emergent classification guardrails, refinement default off.

### Breaking changes

- **Folder layout: domain-scoped sessions now nest under `Areas/`.** Previous: `<vault>/<domain>/sessions/`. New: `<vault>/Areas/<domain>/sessions/`. This matches the PARA mental model (domains are Areas-of-responsibility) and Elio's expectation. **Migration:** `mv <domain>/ Areas/` for each configured domain in your existing vault. See README "Migrating from v0.2.x" for the full procedure.
- **`refinement_enabled` defaults to `false`.** Rule-based refinement was making text worse on Elio's pt-BR braindumps (95% identical to original, sometimes lossy). Default is off until LLM-based semantic refinement lands as a future feature. Existing vaults with `refinement_enabled: true` in `CLAUDE.md` are unaffected.

### Fixed

- **Emergent classification picked Portuguese stopwords as themes.** `_classify_emergent` filtered only English stopwords, so pt-BR braindumps surfaced pronouns ("eu") and conjunctions ("que") as proposed themes. v0.3.0 makes `_STOPWORDS` language-keyed (en, pt-BR, pt) and threads `config.language` through tokenization.

### Added

- **Emergent theme thresholds:** proposed themes now require minimum 4-character word length and minimum 2 occurrences in the text. Tokens that don't meet both thresholds return empty (uncertain), so the user picks the theme rather than getting "que" or "eu" auto-proposed.
- **README:** "What is `sessions/` for?" mental-model paragraph; "Migrating from v0.2.x" section; updated path table reflecting `Areas/<domain>/sessions/`.

## [0.2.2] - 2026-05-04

Skill invocation guidance: explicit delegation pattern to keep token cost bounded.

### Added

- SKILL.md `Invocation pattern (token-cost note)` section documenting that conversational invocations should shell out to `scripts/intake.py --inbox` via the Bash tool rather than reading inbox file contents into context.
- Triggered by Elio Almeida's W5 follow-up question (2026-05-04): how does token cost scale with note size? The honest answer is "it shouldn't" — but only if the skill is invoked via delegation, not by Claude reading files first. This patch makes that pattern explicit.

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
