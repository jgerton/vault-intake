---
name: vault-intake
description: Universal capture skill for Second-Brain vaults. Use this whenever the user wants to process, organize, save, or route content into their vault, including pasted text, brain dumps, transcriptions, session captures, or files in `_inbox/`. Triggers on phrases like "process this," "organize this," "put this in the vault," "save to inbox," "vault intake," "/vault-intake," and on file-drop workflows where the user expects messy input to become organized notes with frontmatter, wikilinks, and routing. Supports two configurable modes per vault: emergent (theme-based, minimal structure) and fixed_domains (configured domain set with PARA routing).
---

# vault-intake

Memory Branch Milestone 1 (M1) skill: trusted capture for Second-Brain vaults. Takes messy input (pasted text, brain dumps, `_inbox/` files) and produces organized notes with frontmatter, wikilinks, and routing per the active vault mode.

## Status

**Implementation in progress.** Step 1 (config resolve and validate) is implemented and tested. Steps 2 through 9 are pending. Do not invoke this skill end-to-end against a real vault until all steps land. The Step 1 helper is usable on its own to validate a vault's CLAUDE.md config.

## Spec references

- **Build spec:** `E:/Projects/ai-asst/brand-toolkit-collab/2026-04-23/17-vault-intake-design-requirements.md`
- **Architecture plan:** `E:/Projects/ai-asst/agentic-os-plan/01-agentic-os-architecture-plan.md` (especially Section 1.4.1 frontmatter baseline, Section 1.5 run artifact contract, Section 1.6 cross-cutting requirements)
- **Cross-cutting requirements** apply: schema migration and versioning, provenance and audit trail, burst-use compatibility, synthesis as only mandatory human step, `/status` as first-class diagnostic, PII baseline.

## Two modes (locked 2026-04-28)

The skill supports two opinionated defaults selectable per vault. Single codebase, mode picked from vault CLAUDE.md.

| Aspect | Emergent | Fixed_domains |
|---|---|---|
| Default user | Elio's personal instance; advanced users | Generalized YCAH install; newcomers |
| Vault structure | `_inbox/`, `_sinteses/`, plus emergent folders | `sessions/`, `insights/`, `workflows/`, `prompts/`, `context/`, `projects/`, `references/` |
| Classification | Themes inferred dynamically | Configured domain set with PARA |
| Frontmatter | `theme` field; type inferred (open) | `domain` field from configured set; type closed enum |

Mode is determined at config-resolve time (Step 1) by the (`classification_mode`, `routing_mode`) pair in vault CLAUDE.md. Only two pairs are supported:

- `(fixed_domains, para)` resolves to internal mode `fixed_domains`
- `(emergent, emergent)` resolves to internal mode `emergent`

Any other pair raises a config error. This is the Option Z lock: two config keys preserved at the vault surface for spec compatibility, single internal mode for clean code paths.

## Config format

Each vault's CLAUDE.md must contain a `## Vault Config` heading followed by a fenced YAML code block. Example:

```markdown
## Vault Config

​```yaml
vault_path: /absolute/path/to/this/vault
classification_mode: fixed_domains   # or: emergent
routing_mode: para                   # or: emergent
domains:                             # required if classification_mode == fixed_domains
  - slug: alpha
    description: First domain description
  - slug: beta
    description: Second domain description
notebook_map:                        # optional; classification key → NotebookLM notebook ID
  alpha: nb-alpha-id
language: en                         # default: en
skip_notebooklm: false               # default: false
refinement_enabled: true             # default: true (Step 2 brain-dump refinement)
​```
```

Required fields: `vault_path`, `classification_mode`, `routing_mode`, plus `domains` if `classification_mode: fixed_domains`. All other fields have defaults.

## Step 1: Config resolve and validate (implemented)

To resolve and validate a vault's config, run the helper script:

```bash
uv run scripts/resolve_config.py <path-to-vault-CLAUDE.md>
```

On success, prints resolved config as JSON to stdout and exits 0:

```json
{
  "vault_path": "/absolute/path/to/vault",
  "mode": "fixed_domains",
  "domains": [{"slug": "alpha", "description": "..."}],
  "notebook_map": {"alpha": "nb-alpha-id"},
  "language": "en",
  "skip_notebooklm": false,
  "refinement_enabled": true
}
```

On any config error (missing required field, malformed YAML, unsupported mode pair, fixed_domains without domains, etc.), prints a clear message to stderr and exits non-zero. Surface the error to the user verbatim; do not attempt to repair vault config silently.

The Python module `vault_intake.config` exposes `resolve_config(path: Path) -> Config` for direct use from other scripts. See `src/vault_intake/config.py` for the dataclass shapes.

## Steps 2 through 9 (pending)

Pipeline overview, to be implemented in subsequent commits:

2. **Refine** if input is a transcription or unstructured brain dump. Preserve original verbatim under `## Captura original`.
3. **Classify** mode-dependent: domain (fixed_domains) or theme (emergent).
4. **PARA category** if `routing_mode: para` (skipped in emergent).
5. **Generate frontmatter** mode-dependent shape; OS-wide baseline plus track-specific additions.
6. **Generate wikilinks** mode-aware; cross-domain or cross-theme top-weighted.
7. **Extract candidate next-actions** gated by action signals only.
8. **Route to destination folder** mode-dependent.
9. **NotebookLM integration** opt-in with graceful degradation.

Each step is documented in detail in the M1 build spec.

## Safety rules (consolidated, apply across all steps)

1. Never edit user's original content. Preserve verbatim in `## Captura original` block.
2. Ask one question when classification uncertain, not a list of options.
3. Never silently guess domain, PARA, or type below confidence threshold.
4. Never block on NotebookLM failure; skip gracefully.
5. Never write a note without user confirmation of draft (unless batch mode has pre-approval).
6. Never cross into another vault or write outside `vault_path`.

## Triggers

The skill is designed to be invocable via:

1. Slash command: `/vault-intake`
2. Natural language in chat ("process this," "organize this," "put this in the vault," "save to inbox")
3. File drop into `_inbox/` followed by trigger 1 or 2
4. Batch mode: process all files in `_inbox/`
5. External call (future): from a hook, scheduled agent, or orchestration layer

## Development

- Source: `src/vault_intake/`
- CLI scripts: `scripts/`
- Tests: `tests/` (run with `uv run pytest`)
- TDD discipline per Jon's CLAUDE.md core principles: failing tests before implementation.
