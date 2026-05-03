# Changelog

All notable changes to vault-intake are documented here.

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
