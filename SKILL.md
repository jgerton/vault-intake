---
name: vault-intake
description: Memory Branch M1 work-in-progress. Step 0 (Bootstrap: config resolve and validate) and pipeline Steps 1 (Detect content type) and 2 (Refine) are implemented. Step 0 parses a Second-Brain vault's CLAUDE.md `## Vault Config` YAML block, enforces the Option Z mode pair lock, and returns resolved JSON. Step 1 classifies raw input into one of seven closed-enum content types and surfaces an uncertainty flag when signals overlap. Step 2 produces a readability-pass refinement of oral or brain-dump content while preserving the verbatim original. Use this skill when the user asks to "validate vault config," "check vault CLAUDE.md," "resolve vault-intake config," "detect vault-intake content type," or "refine vault-intake content" against specific input. Do not use this skill for general capture, intake, or routing tasks; the spec's pipeline Steps 3 through 9 (classify, PARA, frontmatter, wikilinks, next-actions, route, NotebookLM) are not yet implemented and will land in subsequent commits.
---

# vault-intake

Memory Branch Milestone 1 (M1) skill, in progress. The full design is a universal capture skill for Second-Brain vaults. The spec's pipeline runs Steps 1 through 9; Step 0 (Bootstrap: config resolve and validate) is a precondition implemented as part of this skill, not part of the numbered pipeline. Step 0 and pipeline Steps 1 and 2 are implemented and usable; Steps 3 through 9 remain.

## Status

| Step | Status |
|---|---|
| 0. Bootstrap: config resolve and validate | Implemented |
| 1. Detect content type | Implemented |
| 2. Refine (transcription / brain dump) | Implemented |
| 3. Classify (mode-dependent) | Not implemented |
| 4. PARA category | Not implemented |
| 5. Generate frontmatter | Not implemented |
| 6. Generate wikilinks | Not implemented |
| 7. Extract candidate next-actions | Not implemented |
| 8. Route to destination folder | Not implemented |
| 9. NotebookLM integration | Not implemented |

Do not invoke this skill end-to-end against a real vault. Only the Step 0 (Bootstrap), Step 1 (Detect content type), and Step 2 (Refine) helpers are safe to use today; all three produce intermediate output rather than vault writes.

## Spec references

- **Build spec:** `E:/Projects/ai-asst/brand-toolkit-collab/2026-04-23/17-vault-intake-design-requirements.md`
- **Architecture plan:** `E:/Projects/ai-asst/agentic-os-plan/01-agentic-os-architecture-plan.md` (Section 1.4.1 frontmatter baseline, Section 1.5 run artifact contract, Section 1.6 cross-cutting requirements, Section 6.1 Decision 9 mode lock)

## Mode design (Option Z)

The skill supports two opinionated defaults selectable per vault. Single codebase, mode picked from vault CLAUDE.md.

| Aspect | Emergent | Fixed_domains |
|---|---|---|
| Default user | Elio's personal instance; advanced users | Generalized YCAH install; newcomers |
| Vault structure (planned) | `_inbox/`, `_sinteses/`, plus emergent folders | `sessions/`, `insights/`, `workflows/`, `prompts/`, `context/`, `projects/`, `references/` |
| Classification (planned) | Themes inferred dynamically | Configured domain set with PARA |
| Frontmatter (planned) | `theme` field; type inferred (open) | `domain` field from configured set; type closed enum |

Mode is determined at config-resolve time (Step 1) by the (`classification_mode`, `routing_mode`) pair in vault CLAUDE.md. Only two pairs are supported:

- `(fixed_domains, para)` resolves to internal mode `fixed_domains`
- `(emergent, emergent)` resolves to internal mode `emergent`

Any other pair raises a config error. Two config keys are preserved at the vault surface for spec compatibility; a single internal mode is exposed for clean code paths.

## Config format

Each vault's CLAUDE.md must contain a single `## Vault Config` heading followed by a fenced YAML code block. Example:

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
notebook_map:                        # optional; classification key, NotebookLM notebook ID
  alpha: nb-alpha-id
language: en                         # default: en
skip_notebooklm: false               # default: false
refinement_enabled: true             # default: true (Step 2 brain-dump refinement)
​```
```

Required fields: `vault_path` (must be absolute), `classification_mode`, `routing_mode`, plus `domains` if `classification_mode: fixed_domains`. All other fields have defaults.

Constraints enforced by Step 1:

- Exactly one `## Vault Config` heading must appear in CLAUDE.md (multiple is an error).
- The heading must be immediately followed by a fenced ```yaml block.
- The fence must be closed.
- The YAML root must be a mapping (object), not a scalar or list.
- Each entry in `domains` must be a mapping with both `slug` and `description` fields.
- Mode pair must be one of the two supported combinations.

## Step 0: Bootstrap (config resolve and validate)

Step 0 is a precondition for the spec's numbered pipeline. It must succeed before any of Steps 1 through 9 can run.

To resolve and validate a vault's config, run the helper script from this skill's directory:

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

On any config error (missing required field, malformed YAML, unsupported mode pair, fixed_domains without domains, multiple Vault Config blocks, unterminated fence, non-mapping YAML root, malformed domain entry, etc.), prints a clear message to stderr and exits non-zero. Surface the error to the user verbatim; do not attempt to repair vault config silently.

The Python module `vault_intake.config` exposes `resolve_config(path: Path) -> Config` for direct use from other Python code. See `src/vault_intake/config.py` for the dataclass shapes.

## Step 1: Detect content type

Step 1 classifies raw input into one of seven closed-enum content types per build spec lines 56-68:

| Type | Signal |
|---|---|
| `session` | conversational structure with user/assistant turns |
| `document` | clear sections and headings (markdown structure) |
| `reference` | URL, citation, external author |
| `context` | first-person decision phrasing ("I decided," "my position is," "for client X we do Y") |
| `prompt` | directive phrasing for another tool ("send this to," "prompt for," "use this with") |
| `transcription` | length above 300 words plus informal connectives ("e," "aí," "então," "tipo") with no markdown |
| `note` | default when no stronger signal fires |

The Python module `vault_intake.detect` exposes `detect_content_type(text: str) -> DetectionResult` for direct use:

```python
from vault_intake.detect import detect_content_type

result = detect_content_type(input_text)
result.type                    # one of the 7 ContentType literals
result.uncertain               # True when signals overlapped across types
result.signals                 # tuple of detected signal names for the winning type
result.refinement_applicable   # True for transcriptions and brain-dump notes; gates Step 2
```

When `uncertain` is True, the skill must ask the user a single confirmation question with the detected type as the proposed answer (consolidated safety rule 2). Do not present a multi-option list. When `refinement_applicable` is True, Step 2 (Refine) runs after user confirmation; otherwise the pipeline skips Step 2.

`refinement_applicable` is True for `transcription` always, and for `note`, `context`, or `prompt` when the input passes the brain-dump check (no markdown headings, at least twenty words). It is False for `document`, `reference`, and `session` always, and for `note`, `context`, or `prompt` that do not meet the brain-dump threshold. Step 1 makes the gating decision so callers do not duplicate the heuristic.

## Step 2: Refine

Step 2 produces a readability-pass version of oral or brain-dump content per build spec lines 70-84. It runs only when both `Config.refinement_enabled` is True and `DetectionResult.refinement_applicable` is True; the skill orchestrator gates the call. The function itself is unconditional and assumes its caller has already decided refinement is applicable.

The refined version replaces the primary body of the note. The verbatim original is preserved by the caller under a `## Captura original` block at the bottom of the final markdown so the user can revert at any time.

Refinement rules (rule-based v1):

- Light filler removal: `tipo`, `aí`, `né`, and the multiword `e aí` are stripped only at word boundaries (Python `\b`), so substrings like `típico`, `país`, and `tipos` are preserved.
- Conservative paragraph segmentation at sentence-end punctuation followed by an oral-monologue connective (`e`, `aí`, `então`, `tipo`).
- Soft cap of five sentences per paragraph: when no connective signal fires, a paragraph break is forced after every fifth sentence so wall-of-text monologues still gain structure.
- Pre-existing `\n\n` paragraph breaks are preserved.

Six non-negotiable safety rules apply (spec lines 73-79):

1. Never edit the user's original content; the original is returned unchanged in `RefinedContent.original`.
2. Remove only filler that is pure noise.
3. Preserve all ideas, even partial or contradictory.
4. Do not editorialize, summarize, or interpret.
5. Do not add information not in the original.
6. Do not remove items because they seem off-topic.

The Python module `vault_intake.refine` exposes `refine(text: str) -> RefinedContent`:

```python
from vault_intake.refine import refine

result = refine(input_text)
result.refined    # readability-pass version (paragraph-broken, light filler removal)
result.original   # verbatim original, never edited
result.changed    # True when refined != original
```

Skill template assembles the final markdown after Step 2:

```
{result.refined}

## Captura original

{result.original}
```

Skip Step 2 entirely when `Config.refinement_enabled` is False or `DetectionResult.refinement_applicable` is False; do not call `refine()` at all in those cases. The function itself does not duplicate the gate.

## Pipeline (Steps 3 through 9, planned)

Documented for reference; not implemented yet. Each will land in subsequent commits with its own tests. Steps 1 and 2 are described in their own sections above.

3. **Classify** mode-dependent: domain (fixed_domains) or theme (emergent).
4. **PARA category** if `routing_mode: para` (skipped in emergent).
5. **Generate frontmatter** mode-dependent shape; OS-wide baseline plus track-specific additions.
6. **Generate wikilinks** mode-aware; cross-domain or cross-theme top-weighted.
7. **Extract candidate next-actions** gated by action signals only.
8. **Route to destination folder** mode-dependent.
9. **NotebookLM integration** opt-in with graceful degradation.

Each step is documented in detail in the M1 build spec. Multi-invocation paths (slash command, natural language, file drop, batch, external call) are designed for the full pipeline but not in scope for the current commit.

## Safety rules (consolidated, apply across all steps when implemented)

1. Never edit user's original content. Preserve verbatim in `## Captura original` block.
2. Ask one question when classification uncertain, not a list of options.
3. Never silently guess domain, PARA, or type below confidence threshold.
4. Never block on NotebookLM failure; skip gracefully.
5. Never write a note without user confirmation of draft (unless batch mode has pre-approval).
6. Never cross into another vault or write outside `vault_path`.

## Development

- Source: `src/vault_intake/`
- CLI scripts: `scripts/`
- Tests: `tests/` (run with `uv run pytest`)
- TDD discipline per Jon's CLAUDE.md core principles: failing tests before implementation.
