---
name: vault-intake
description: Memory Branch M1 work-in-progress. Step 0 (Bootstrap: config resolve and validate) and pipeline Steps 1-9 are implemented as library APIs, plus a two-entrypoint orchestrator that wires Steps 0-9 into the spec's output contract per build spec lines 228-243. The dry-run entrypoint `run_intake(input_text, config, ...) -> IntakeRun` produces the assembled markdown body, the proposed destination, the questions tuple for uncertainty escalation, and the queue surface for NotebookLM auth-error retries; it never touches the filesystem (Step 9 always runs with `note_path=None` and returns a skipped result). The post-confirmation entrypoint `confirm_and_write(intake_run, config, ...) -> IntakeRun` writes the file atomically (temp file plus os.replace), re-invokes Step 9 live against the written path, threads any returned `source_id` back into frontmatter, and re-writes the file. Section-update mode (context+project routes to `projects/{slug}.md`) appends a section to the existing project hub and skips live Step 9 to avoid duplicate NotebookLM sources. Defense-in-depth check enforces spec safety rule 6 (destination must be inside `vault_path`). Step 0 parses a Second-Brain vault's CLAUDE.md `## Vault Config` YAML block, enforces the Option Z mode pair lock, and returns resolved JSON. Step 9 looks up `classification.primary` in `config.notebook_map`, runs an auth precheck via `notebooklm auth check --test`, calls `notebooklm source list` to enforce a 45/50 source-count warning, and adds the note via `notebooklm source add ... --json`; on auth failure (precheck or runtime), the action is queued at `<vault>/.vault-intake/nlm_queue/` for later drain via `flush_nlm_queue`, while non-auth failures (timeout, JSON parse error, source-count exhausted) return failed without queueing. Step 1 classifies raw input into one of seven closed-enum content types and surfaces an uncertainty flag when signals overlap. Step 2 produces a readability-pass refinement of oral or brain-dump content while preserving the verbatim original. Step 3 classifies fixed_domains-mode content into a primary domain plus secondary tags using rule-based keyword matching, with a configurable confidence threshold and an uncertainty flag for caller-driven confirmation. Step 4 categorizes content into one of four PARA buckets (project, area, resource, archive) using rule-based heuristics over the project inventory under `vault_path/projects/`, the upstream detection result, and superseded-decision phrasing; emergent mode skips PARA entirely and raises NotImplementedError on direct call. Step 5 builds a frozen `Frontmatter` dataclass populated from the upstream pipeline outputs plus capture metadata, emitting the OS-wide canonical baseline (architecture plan Section 1.4.1) and the fixed_domains track-specific additions (build spec lines 122-135) with a kebab-case title heuristic, capped tags, and a `to_yaml()` serializer; emergent mode raises NotImplementedError. Step 6 walks the vault, parses each markdown file's frontmatter, and produces ranked wikilink proposals (cross-domain weight 4, active project weight 3, concept overlap weight 2 at a 2-token floor, empty backlog markers from typed `[[X]]` weight 1) capped at 7 with dedupe by target and recency-then-alphabetical tiebreaks; emergent mode raises NotImplementedError. Step 7 scans the body for action signals (imperatives, future-tense intent, dates and deadlines, decision points, named follow-ups), produces a seed list of candidate next-actions, and renders them as plain bullets under "Possíveis próximos passos"; mode-agnostic. Step 8 routes notes to a destination folder via the spec's (type, PARA) destination table in fixed_domains mode and via theme-folder lookup with `_inbox/` fallback in emergent mode; path-suggestion only with no filesystem side effects. Use this skill when the user asks to "validate vault config," "check vault CLAUDE.md," "run vault-intake," "intake into vault," "resolve vault-intake config," "detect vault-intake content type," "refine vault-intake content," "classify vault-intake content," "categorize vault-intake PARA," "generate vault-intake frontmatter," "generate vault-intake wikilinks," "extract vault-intake next-actions," "route vault-intake content," or "integrate vault-intake with NotebookLM" against specific input. End-to-end use is two calls: `run_intake` for the previewable dry-run result, then `confirm_and_write` to land the file. Users invoke the pipeline through `scripts/intake.py` (the explicit-confirmation surface that satisfies spec safety rule 5 by prompting between the two entrypoints) and `scripts/flush_nlm.py` (manual NotebookLM retry-queue drain). `scripts/install_skill.py` syncs these artifacts plus the library to a Claude Code skills directory so the skill is invocable as `/vault-intake` from any session.
---

# vault-intake

Memory Branch Milestone 1 (M1) skill, in progress. The full design is a universal capture skill for Second-Brain vaults. The spec's pipeline runs Steps 1 through 9; Step 0 (Bootstrap: config resolve and validate) is a precondition implemented as part of this skill, not part of the numbered pipeline. Step 0 and pipeline Steps 1, 2, 3 (Classify, fixed_domains mode only), 4 (PARA category, fixed_domains/para mode only), 5 (Generate frontmatter, fixed_domains track only), 6 (Generate wikilinks, fixed_domains track only), 7 (Extract candidate next-actions, mode-agnostic), 8 (Route to destination folder, both modes), and 9 (NotebookLM integration, mode-agnostic) are implemented and usable as library APIs. The orchestrator that wires them into the spec's output contract per build spec lines 228-243 is also implemented as two entrypoints. `run_intake(input_text, config, ...) -> IntakeRun` produces the dry-run result (assembled markdown body, proposed destination, questions tuple, queue surface) and never touches the filesystem; Step 9 runs with `note_path=None` and returns a skipped result. `confirm_and_write(intake_run, config, ...) -> IntakeRun` is the post-confirmation entrypoint that performs the atomic file write, re-invokes Step 9 live against the written path, and re-writes the file when Step 9 returns a `source_id`. Section-update mode (context+project routes to `projects/{slug}.md`) appends a section to the existing project hub and skips live Step 9. Emergent-mode classification, the emergent frontmatter shape, and emergent-mode wikilinks are parallel tracks that land in separate sessions once fixed_domains stabilizes; the orchestrator catches NotImplementedError from each emergent step and surfaces the cascade as questions so emergent-mode runs degrade gracefully. Step 7 was the first mode-agnostic step; Step 8 was the first step shipping both modes simultaneously (emergent routing only needs theme-folder lookup with inbox fallback). Step 9 is mode-agnostic since both modes use the same `classification.primary` lookup key.

## Status

| Step | Status |
|---|---|
| 0. Bootstrap: config resolve and validate | Implemented |
| 1. Detect content type | Implemented |
| 2. Refine (transcription / brain dump) | Implemented |
| 3. Classify (mode-dependent) | Implemented (fixed_domains only; emergent raises NotImplementedError) |
| 4. PARA category | Implemented (fixed_domains/para only; emergent raises NotImplementedError) |
| 5. Generate frontmatter | Implemented (fixed_domains track only; emergent raises NotImplementedError) |
| 6. Generate wikilinks | Implemented (fixed_domains track only; emergent raises NotImplementedError) |
| 7. Extract candidate next-actions | Implemented (mode-agnostic; both modes share the same code path) |
| 8. Route to destination folder | Implemented (both modes; path-suggestion only, no filesystem side effects) |
| 9. NotebookLM integration | Implemented (mode-agnostic; opt-in with graceful degradation; auth precheck and persistent retry queue) |
| Orchestrator: `run_intake` (dry-run) | Implemented (fixed_domains end-to-end; emergent-mode runs degrade gracefully via NotImplementedError catch with full Steps 3-6 cascade surfaced as questions) |
| Orchestrator: `confirm_and_write` (post-confirmation file write) | Implemented (atomic write via temp file plus os.replace; live Step 9 invocation with frontmatter source_id mutation and atomic re-write; section-update path for context+project routes; defense-in-depth check destination is inside vault_path) |
| CLI wrappers (`scripts/intake.py`, `scripts/flush_nlm.py`, `scripts/install_skill.py`) | Implemented (intake.py is the spec-safety-rule-5 explicit-confirmation surface; flush_nlm.py drains the NotebookLM retry queue; install_skill.py syncs skill artifacts to `~/.claude/skills/vault-intake/` so `/vault-intake` is live-invocable) |

The orchestrator wires Steps 0-9 into two entrypoints that together return the spec's output contract per build spec lines 228-243. `run_intake(input_text, config, ...) -> IntakeRun` is the dry-run pass: `IntakeRun.written_path` is always None, and Step 9 always runs with `note_path=None` (returns a `skipped("dry-run...")` result). `confirm_and_write(intake_run, config, ...) -> IntakeRun` is the post-confirmation pass that writes the file, re-invokes Step 9 live, and re-writes the file with the updated YAML when Step 9 returns a `source_id`. User-invocable CLI wrappers `scripts/intake.py` and `scripts/flush_nlm.py` ship the explicit-confirmation surface between the two entrypoints (spec safety rule 5) and the manual NotebookLM retry-queue drain respectively; see the Invocation section below. Library helpers remain individually safe to use against a real vault.

## Invocation (CLI wrappers)

Three user-invocable wrappers live in `scripts/`. After install (`scripts/install_skill.py` syncs the skill to `~/.claude/skills/vault-intake/`), the wrappers run from the install location and resolve their `vault_intake.*` imports through the install's `pyproject.toml` plus `uv` lazy venv setup.

### `scripts/intake.py`

End-to-end intake CLI; the explicit-confirmation surface for spec safety rule 5. Reads input from stdin or `--input PATH`, runs the orchestrator's dry-run pass, prompts the user (unless `--yes`), then commits via `confirm_and_write`.

Flags:

- `--vault PATH` (env fallback `VAULT_INTAKE_VAULT_PATH`)
- `--input PATH` (explicit file wins over piped stdin; TTY stdin without `--input` refuses with exit 2)
- `--source-type {vault,paste,stdin,api,external_cli,other}` (validated against the `SourceType` Literal in `frontmatter.py`)
- `--source-uri TEXT`
- `--title TEXT` (overrides the title heuristic; rejected if empty or whitespace)
- `--yes` (pre-approve writes; spec safety rule 5 documented opt-in)
- `--overwrite` (allow overwriting an existing destination file)
- `--dry-run` (run pipeline, print summary, do not write)
- `--nlm-command CMD` (override NotebookLM CLI binary; defaults to `notebooklm`)
- `--skip-notebooklm` (override per-vault config to skip Step 9 for this run)

Exit codes: 0 success / 1 user aborted / 2 config error / 3 pipeline error / 4 file write error.

### `scripts/flush_nlm.py`

Manual drain command for the NotebookLM retry queue at `<vault>/.vault-intake/nlm_queue/`. Run after `notebooklm login` to push any queued items.

Flags:

- `--vault PATH` (env fallback `VAULT_INTAKE_VAULT_PATH`)
- `--nlm-command CMD`

Prints summary line `processed: N / still_queued: N / dropped: N`. When `still_queued > 0`, prints per-entry log lines with `notebook=ID note=PATH retry_count=N`.

Exit codes: 0 drain attempted / 2 config error.

### `scripts/install_skill.py`

Copy / sync helper that ships the skill artifacts (`SKILL.md`, `pyproject.toml`, `uv.lock`, `src/vault_intake/`, `scripts/`) from the dev repo to a Claude Code skills directory (default `~/.claude/skills/vault-intake/`). Allowlist-based; `tests/`, `references/`, `.git/`, `.venv/`, `__pycache__/` are not copied. Idempotent; re-run to sync after dev changes.

Flags:

- `--source PATH` (default: this script's parent repo)
- `--dest PATH` (default: `~/.claude/skills/vault-intake/`)

Exit codes: 0 success / 2 source missing or required allowlisted file absent / 4 destination write error.

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
classification_confidence_threshold: 0.6  # default: 0.6; Step 3 confidence below this flips uncertain=True
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

## Step 3: Classify (mode-dependent)

Step 3 classifies refined or unrefined content into a primary domain (fixed_domains mode) or theme (emergent mode) per build spec lines 85-105. Step 3 runs after Step 1 regardless of `refinement_applicable`; types like `session`, `document`, and `reference` skip Step 2 but still need classification.

**fixed_domains mode (v1, implemented):**

Rule-based keyword matching against each configured domain's slug and description. Picks the highest-scoring domain as primary, includes other domains as secondary tags when their score is at least 40 percent of the primary score, and surfaces a confidence value plus an uncertainty flag.

Scoring details:

- The input and each domain's `slug + description` are tokenized to lowercase word sets with a small English stop-word filter (`the`, `and`, `is`, etc.) so common connectors do not inflate scores.
- Domain score = number of distinct vocab tokens that appear in the input, plus a fixed bonus when the literal slug is mentioned in the input. Slug mentions outweigh description-only token matches.
- Confidence = `primary_score / max(min_evidence_floor, primary_score + runner_up_score)`. The evidence floor (5) keeps confidence below 1.0 when the absolute hit count is sparse, even when the primary is unchallenged.
- `uncertain = confidence < classification_confidence_threshold` (config field, default 0.6). When `uncertain` is True the caller asks one confirmation question per consolidated safety rule 2 and 3.
- When all domain scores are zero, primary defaults to the first-listed domain in `Config.domains` and confidence is 0.0 (always uncertain).

**emergent mode (v1, not implemented):**

Calling `classify(text, config)` with `config.mode == "emergent"` raises `NotImplementedError`. Emergent classification (read existing themes from `_sinteses/`, emergent folder names, and `theme` frontmatter; match or propose a new theme; never auto-create folders) lands in a parallel session track once fixed_domains stabilizes.

The Python module `vault_intake.classify` exposes `classify(text: str, config: Config) -> ClassificationResult`:

```python
from vault_intake.classify import classify

result = classify(input_text, config)
result.primary      # domain slug (fixed_domains) or theme name (emergent)
result.secondary    # tuple of secondary domain or theme tags
result.confidence   # 0.0 to 1.0
result.uncertain    # True when confidence < config.classification_confidence_threshold
result.mode         # "fixed_domains" or "emergent"
```

## Step 4: Categorize PARA category (mode-gated)

Step 4 categorizes content into one of four PARA buckets per build spec lines 107-117. It runs only in `fixed_domains/para` mode; emergent mode routes by theme in Step 8 instead, so calling `categorize_para()` with `config.mode == "emergent"` raises `NotImplementedError`. The function-side gate is unconditional; the skill orchestrator decides whether to call.

**fixed_domains/para mode (v1, implemented):**

Rule-based heuristics inspecting the raw text plus the upstream `DetectionResult` and `ClassificationResult`. Strong signals fire when:

- `project_slug_match`: input mentions a slug found in `vault_path/projects/` (file stem or folder name; case-insensitive; word-boundary). When the projects directory is missing, the signal cannot fire.
- `reference_content_type`: `DetectionResult.type == "reference"`.
- `archive_phrasing`: lowercased text contains "we used to," "old approach was," "deprecated," "no longer used," or "superseded."

Category priority among strong signals: project > resource > archive > area. Area is the default when no strong signal fires. The `signals` field captures every signal that fired (including a descriptive `domain_in_scope` flag when the area default is backed by a confident classification primary), so the audit trail is preserved even when the winning category is set by priority.

`uncertain` flips True when more than one strong signal fires (multiple categories competing) or when the result falls back to area without classification confidence (no project, no reference, no archive phrasing, classification uncertain or primary outside configured domains).

When category is `project`, `project_slug` is set to the matched slug; otherwise `project_slug` is None. When multiple project slugs match the input, the alphabetically-first match is returned for determinism.

**emergent mode (v1, not applicable):**

`categorize_para(text, detection, classification, config)` raises `NotImplementedError` when `config.mode == "emergent"`. PARA is a fixed_domains/para construct; emergent vaults never call it.

The Python module `vault_intake.para` exposes `categorize_para`:

```python
from vault_intake.para import categorize_para

result = categorize_para(input_text, detection, classification, config)
result.category       # "project" | "area" | "resource" | "archive"
result.project_slug   # slug string when category == "project", else None
result.uncertain      # True when multiple strong signals fire or area-default lacks evidence
result.signals        # tuple of fired signal names (audit trail)
```

## Step 5: Generate frontmatter (mode-dependent)

Step 5 builds the canonical frontmatter for the captured note per build spec lines 118-153. The OS-wide baseline (architecture plan Section 1.4.1) plus the fixed_domains track-specific additions are populated from the upstream pipeline outputs plus capture metadata. Emergent mode raises `NotImplementedError`; the emergent shape (uses `theme` instead of `domain`, with an open `type` enum) lands in a parallel session.

**fixed_domains track (v1, implemented):**

Rule-based deterministic builder. Inputs:

- `text`: the input body, used by the title heuristic (callers pass the refined body when refinement applied, otherwise the original).
- `detection: DetectionResult`: drives the frontmatter `type` field through the translation table below; `document` and `transcription` map to `note` because both describe stage rather than destination.
- `refinement: RefinedContent | None`: None when Step 2 was skipped (already-structured types or `refinement_enabled=False`); otherwise carries `changed`. When `changed` is True, `original_ref` is set to `## Captura original` so downstream consumers know to expect the verbatim block; otherwise empty.
- `classification: ClassificationResult`: `primary` populates `domain`; `(primary,) + secondary` populates `tags` (capped at 5); `confidence` populates the OS-wide `confidence` field. When classification is uncertain, tags are emitted empty so the user fills in at confirmation.
- `para: ParaResult`: when category is `project`, `project_slug` populates the `project` field and the frontmatter `type` overrides to `project` regardless of detection so routing and type stay consistent; otherwise the project field is empty.
- `config: Config`: `notebook_map` resolves `domain` to `notebook` (empty string on miss); `mode` gates the function.

Type derivation table (build spec line 128's 8-value enum):

| Detection type | Frontmatter type | Notes |
|---|---|---|
| `session` | `session` | passes through |
| `document` | `note` | document is structural, not destination |
| `reference` | `reference` | passes through |
| `context` | `context` | passes through |
| `prompt` | `prompt` | passes through |
| `transcription` | `note` | refinement-stage signal, not destination |
| `note` | `note` | passes through |
| any (PARA project override) | `project` | when `para.category == "project"` |

`insight` and `workflow` are valid frontmatter types but are not auto-derived in v1; the skill orchestrator surfaces them as user-selectable options at confirmation. v2 may add heuristics.

Capture metadata is keyword-only: `source_type` (default `"paste"`), `source_uri` (default `""`), `captured_at` (default today's date in ISO format).

Title generation (build spec line 153 "concise, descriptive, kebab-case slug for filename"):

- If the input has a markdown H1 (`# ...`), that line is the title source.
- Otherwise the first sentence (split on `.!?` followed by whitespace) is the title source.
- The source is NFKD-normalized to strip accents (so `Reunião` becomes `reuniao`), lowercased, runs of non-alphanumeric characters collapsed to single hyphens, and trimmed of leading and trailing hyphens.
- Capped at 80 characters; if truncation leaves a trailing hyphen, that hyphen is trimmed.
- Falls back to `note-{captured_at}` when the source is empty after slugification.

The skill orchestrator confirms the title with the user before file write; the heuristic primes the pump.

**emergent track (v1, not implemented):**

`generate_frontmatter(...)` raises `NotImplementedError` when `config.mode == "emergent"`.

The Python module `vault_intake.frontmatter` exposes `Frontmatter` and `generate_frontmatter`:

```python
from vault_intake.frontmatter import generate_frontmatter

fm = generate_frontmatter(
    text=input_text,
    detection=detection,
    refinement=refinement,        # or None
    classification=classification,
    para=para,
    config=config,
    source_type="paste",
    source_uri="",
    captured_at=None,             # defaults to today's ISO date
)
fm.to_yaml()                      # YAML-frontmatter-ready text
```

The `Frontmatter` dataclass is frozen. `confidence` is `float | None`; `to_yaml()` emits empty string for None to satisfy the OS-wide baseline's "optional" rule. `source_id` is always empty at frontmatter-creation time and gets filled by Step 9 (NotebookLM) if the user opts in.

## Step 6: Generate wikilinks (mode-aware)

Step 6 produces ranked wikilink proposals for the new note's "Related" section per build spec lines 155-170. The function walks the vault, parses each markdown file's frontmatter, and scores candidates against four signals; output is capped at 7 (spec line 169) and returned without padding when fewer than the target are found. Emergent mode raises `NotImplementedError`; the emergent shape (theme-based, walks `_sinteses/` and adjacent theme folders) lands in a parallel session.

**fixed_domains track (v1, implemented):**

Walks `vault_path` recursively. Skips dot-prefixed directories (`.git`, `.obsidian`) and the `_indexes/` folder (the v1 source strategy reads real notes' frontmatter rather than curated index files). Walk order is sorted (dirs and files) so the audit trail is deterministic across platforms. Files that fail to read (OSError, UnicodeDecodeError) are skipped entirely; files that read but fail YAML frontmatter parse are retained with no domain and the filename stem as label, so concept-overlap can still fire on the stem. Files are read with `utf-8-sig` so a leading UTF-8 BOM does not block frontmatter detection.

Weighting (highest to lowest, build spec lines 163-167):

| Weight | Signal | Source |
|---|---|---|
| 4 | Cross-domain | Existing note whose `domain` frontmatter is in `classification.secondary` |
| 3 | Active project | When `para.category == "project"` and `para.project_slug` is set; one wikilink per call |
| 2 | Concept overlap | Existing note whose title shares 2+ significant tokens with the body (reuses Step 3's tokenizer and stop-word list) |
| 1 | Empty backlog marker | User-typed `[[X]]` in body that does not match any existing vault note's frontmatter title or filename stem |

Dedupe and tiebreaks:

- Dedupe by `target` string. When a note qualifies for multiple signals, the highest weight wins. When weights tie across two candidates with the same target, the more recent `mtime` wins.
- Sort proposals by weight descending, then `mtime` descending (newer notes first), then alphabetical by `source_path` string. Backlog markers (no source path) sort to the back of their weight band.
- Cap at `max_proposals` (default 7). `min_proposals_target` (default 3) is advisory; the function returns what it has rather than padding with weak fillers.
- `candidates_considered` records the count of unique deduped candidates examined before the cap, for the audit trail.

Title resolution: existing-note `target` is the frontmatter `title` field when set, else the filename stem. Active-project `target` is the project slug; `source_path` resolves to `projects/{slug}.md`, then `projects/{slug}/`, else `None`. Backlog-marker `target` is the user-typed string verbatim (after stripping any `|alias` suffix); `source_path` is `None`.

Concept-overlap heuristic uses the same `_tokenize` and `_STOPWORDS` from `classify.py` so domain semantics stay consistent with Step 3. The 2-token floor suppresses single-token noise.

Empty backlog markers honor the user's explicit intent only; they are emitted only when the user typed `[[X]]`. v1 does not auto-generate markers from arbitrary nouns; v2 may add NER-style concept extraction if dogfood reveals demand.

**emergent track (v1, not implemented):**

`generate_wikilinks(...)` raises `NotImplementedError` when `config.mode == "emergent"`.

The Python module `vault_intake.wikilinks` exposes `Wikilink`, `WikilinkResult`, and `generate_wikilinks`:

```python
from vault_intake.wikilinks import generate_wikilinks

result = generate_wikilinks(
    text=input_text,
    classification=classification,
    para=para,
    config=config,
    max_proposals=7,             # default 7
    min_proposals_target=3,      # advisory; no padding when fewer candidates exist
)
result.proposals                 # tuple[Wikilink, ...] sorted weight desc, capped
result.mode                      # "fixed_domains" or "emergent"
result.candidates_considered     # unique candidates before the cap (audit trail)

# Each Wikilink:
result.proposals[0].target       # the target string used for [[target]]
result.proposals[0].weight       # 1 (backlog) | 2 (concept) | 3 (project) | 4 (cross-domain)
result.proposals[0].source_path  # Path to the contributing note, or None for backlog markers
result.proposals[0].reason       # short human-readable audit string
```

The `Wikilink` and `WikilinkResult` dataclasses are frozen.

## Step 7: Extract candidate next-actions (gated by action signals)

Step 7 scans the note body for action signals and produces a seed list of candidate next-actions per build spec lines 171-182. Gate is internal and content-driven: when no signals fire, the function returns `gate_fired=False` with empty proposals so the skill orchestrator can simply skip appending the section. Spec line 173 explicitly says "Avoid creating empty next-actions sections; intake should not generate task debt."

This is the first mode-agnostic step in the pipeline. Action-signal detection is content-driven, not vault-driven, so fixed_domains and emergent share the same code path; emergent does NOT raise `NotImplementedError`.

**Five gate signals (rule-based v1, Option A; model-call v2 deferred):**

| Signal | Detection |
|---|---|
| `imperative` | First significant word of a sentence (after stripping bullet/list markers) is in the curated imperative-verb list (call, send, review, check, ping, fix, ship, post, write, schedule, decide, ...) |
| `future_intent` | Word-bounded match of "we'll", "i'll", "i should", "we need to", "going to", "i must", etc. |
| `date` | ISO date (`\d{4}-\d{2}-\d{2}`), relative phrase (tomorrow, today, this/next week/weekend/month, by EOW/EOD, by end of week/month/day), day-of-week with prefix (by/on/next/this Monday-Sunday), "by" + month name, or "in N day(s)/week(s)/month(s)" |
| `decision_point` | TBD, "we/I need to decide", "still figuring (it) out", "open question(s)", "to be decided", "undecided", "we/I haven't decided", "we/I haven't figured (it) out", "decide on/whether" |
| `named_followup` | Direct-address verb plus capitalized name (`ping Alice`, `ask Bob`), tool verb plus capitalized name (`test in Playwright`, `spike with Convex`, `deploy on Fly`), or delivery verb followed later in the sentence by `to <Capitalized>` (`Send the deck to Alice`) |

**Result shape:** Frozen `NextAction` (with `what` required and `when`/`where`/`effort`/`waiting_on` optional, plus `signal` and `source_excerpt` for the audit trail) plus frozen `NextActionsResult` (`proposals`, `gate_fired`, `signals_detected` deduplicated and alpha-sorted). When multiple signals fire on the same sentence, `signal` joins the names with " + " (alpha-sorted for stability).

**Output rendering:** `NextActionsResult.to_markdown()` emits `## Possíveis próximos passos` followed by plain bullets (NOT task checkboxes per spec line 175). Each bullet uses the kickoff's bracket-annotation form: `- [What] {what} [Where: ...] [When: ...] [Effort: ...] [Waiting on: ...] [Signal: ...]`. Optional brackets are omitted when their field is None; `[Signal: ...]` is always present. Returns empty string when `gate_fired=False`.

**Empty-input behavior:** Empty or whitespace-only text returns `gate_fired=False` with empty proposals and empty `signals_detected`. The function never raises on benign input.

**Cap:** `max_proposals` is keyword-only with a generous default of 10; spec line 175 says this is a seed list (over-supplying is acceptable; the user prunes at confirmation).

The Python module `vault_intake.next_actions` exposes `NextAction`, `NextActionsResult`, and `extract_next_actions`:

```python
from vault_intake.next_actions import extract_next_actions

result = extract_next_actions(
    text=input_text,
    config=config,
    max_proposals=10,         # default 10
)
result.gate_fired             # True when at least one signal fired
result.proposals              # tuple[NextAction, ...]; capped at max_proposals
result.signals_detected       # tuple[str, ...]; deduplicated, alpha-sorted

# Each NextAction:
result.proposals[0].what            # the action candidate (the source sentence)
result.proposals[0].when            # extracted date phrase, or None
result.proposals[0].where           # extracted person/tool name, or None
result.proposals[0].effort          # scope cue; None in v1 (extraction deferred)
result.proposals[0].waiting_on      # dependency cue; None in v1 (extraction deferred)
result.proposals[0].signal          # joined gate signals (e.g., "date + imperative + named_followup")
result.proposals[0].source_excerpt  # the verbatim slice of input the candidate was extracted from

# Render markdown:
md = result.to_markdown()           # "" when gate did not fire
```

The `NextAction` and `NextActionsResult` dataclasses are frozen.

**v1 deliberate simplifications:**

- Imperative verb list is intentionally narrow; expand only when dogfood surfaces misses.
- Named-followup name capture is constrained to capitalized words (proper-noun heuristic). Lowercase-named entities are missed; the user corrects at confirmation.
- `effort` and `waiting_on` are not extracted in v1; both are always `None`. Extraction lands in v2 if dogfood demand surfaces.
- The `config` argument is accepted for orchestrator parity with Steps 3-6 but is not consulted in v1; both `fixed_domains` and `emergent` produce identical output.

## Step 8: Route to destination folder (mode-dependent)

Step 8 returns a path-suggestion for where the assembled note should land per build spec lines 184-214. The function is pure: it has no filesystem side effects (no folder creation, no file writes). The skill orchestrator handles the actual write at session-end confirmation, including `mkdir(parents=True, exist_ok=True)` for any nonexistent destination folder.

Step 8 is the first dual-mode step: both `fixed_domains` and `emergent` ship in the same commit. The function-side gate is unconditional; the orchestrator picks whether to invoke. In `fixed_domains` mode, `para` is required (raises `ValueError` if `None`); in `emergent` mode, `para` is ignored.

**fixed_domains/para mode (v1, implemented):**

Routes via the spec's (type, PARA) destination table. The routing key uses the canonical `frontmatter.type` (set by Step 5) for most cases, but disambiguates the PARA-project override using `detection.type` so spec line 198's context+project case (section update on `projects/{slug}.md`) stays distinct from spec lines 192/201's session+project and note+project cases (sessions/ + project link).

| Frontmatter type | PARA category | Destination | Notes |
|---|---|---|---|
| `session` | area | `sessions/` | n/a |
| `insight` | any | `insights/` | n/a |
| `workflow` | any | `workflows/` | n/a |
| `prompt` | any | `prompts/` | n/a |
| `context` | area | `context/` | n/a |
| `reference` | resource | `references/` | n/a |
| `note` | area | `sessions/` | spec line 200 treats note as session-equivalent |
| `project` (PARA-project override fired) | project | varies | see below |

When the PARA-project override fires (`para.category == "project"`, so Step 5 sets `frontmatter.type == "project"`), routing uses `detection.type`:

| Detection type | Destination | Project link target | Section update |
|---|---|---|---|
| `context` | `projects/{slug}.md` | `projects/{slug}.md` | True |
| `session` | `sessions/` | `projects/{slug}.md` | False |
| `note` | `sessions/` | `projects/{slug}.md` | False |
| any other | `sessions/` | `projects/{slug}.md` | False |

Archive PARA (`para.category == "archive"`) does not auto-route per spec line 203. The function sets `archive_flagged=True` and routes the destination to the canonical default folder for the frontmatter type (e.g., session → `sessions/`, insight → `insights/`) so the orchestrator can offer the user "route here, or move to archive/."

Unlisted (frontmatter.type, PARA) combinations (e.g., `reference` + `area`, `session` + `resource`) fall back to `_inbox/` with `inbox_fallback=True` and a reason string capturing the unlisted combo for audit. v1 prefers permissive fallback over a strict raise so the pipeline can complete and the user can resolve manually.

**emergent mode (v1, implemented):**

Routes by theme. The function reads `classification.primary` (the inferred theme) and walks `vault_path.iterdir()` looking for a folder whose name matches the theme exactly or after slugification. Underscore-prefixed system folders (`_inbox`, `_sinteses`) are excluded from theme matching, so a theme literally named "inbox" cannot collide with the system inbox. When a folder matches, route there. When no folder matches, route to `vault_path / "_inbox"` with `inbox_fallback=True` (spec lines 209-210). Folder creation for new themes is a separate, intentional consolidation step driven by `/status` review or a future `/consolidate` command, not by intake.

`para` is ignored in emergent mode. If a caller passes a `ParaResult` it is silently disregarded.

**Result shape:** Frozen `RouteResult` with seven fields:

- `destination: Path`: absolute folder Path, or absolute file Path when `is_section_update=True`
- `project_link_target: Path | None`: set for PARA-project routing (session+project, context+project, note+project) so the orchestrator can append a wikilink to the project hub file; `None` otherwise
- `archive_flagged: bool`: True when `para.category == "archive"`; orchestrator surfaces a confirmation prompt rather than auto-routing
- `inbox_fallback: bool`: True when destination is `_inbox/` (unlisted fixed_domains combo, or emergent theme without folder)
- `is_section_update: bool`: True for context+project; orchestrator appends a section to `projects/{slug}.md` rather than creating a new file
- `reason: str`: short human-readable audit string (e.g., `"type=session, para=area, dest=sessions/"`)
- `mode: Mode`: `"fixed_domains"` or `"emergent"`

The Python module `vault_intake.route` exposes `RouteResult` and `route`:

```python
from vault_intake.route import route

result = route(
    detection=detection,
    classification=classification,
    para=para,                  # required in fixed_domains; ignored in emergent
    frontmatter=frontmatter,
    config=config,
)
result.destination              # absolute Path (folder, or .md file when is_section_update)
result.project_link_target      # Path | None
result.archive_flagged          # bool
result.inbox_fallback           # bool
result.is_section_update        # bool
result.reason                   # short audit string
result.mode                     # "fixed_domains" or "emergent"
```

**v1 deliberate simplifications:**

- Spec line 199 lists `references/` OR `pesquisa/` as the reference destination. v1 always routes to `references/`; Portuguese localization (`pesquisa/`) is deferred until a language-aware routing knob is requested.
- Synthesis documents (the `/synth` artifacts per the dossier) are spec'd to live in `_sinteses/` regardless of mode (spec line 214). v1 has no `synthesis` content type, so this rule is deferred until the type lands.
- `route()` has no filesystem side effects. The orchestrator handles `mkdir(parents=True, exist_ok=True)` at write time. This keeps Step 8 stateless and unit-testable.
- Emergent theme-folder matching uses exact name plus a single slug variant (NFKD-normalized, lowercased, non-alphanumeric runs replaced with `-`). Fuzzy matching is deferred.

## Step 9: NotebookLM integration (opt-in, graceful)

Step 9 adds the assembled note as a source to a NotebookLM notebook per build spec lines 216-226. Opt-in (`config.skip_notebooklm` and `config.notebook_map` gates) and graceful (no failure ever blocks the pipeline; spec line 224 contract). Mode-agnostic: both fixed_domains and emergent use `classification.primary` as the lookup key in `config.notebook_map`.

**Auth model and queue rationale:**

The `notebooklm` CLI auth has two layers. CSRF tokens / session IDs expire on the order of minutes but the CLI auto-refreshes them. The underlying Google session cookies expire roughly every few weeks and require manual `notebooklm login`. The self-refresh handles the short-lived layer; we only see real failures when the deep cookies are dead.

Two extensions over the bare spec (signed off 2026-04-30):

1. **Auth precheck** via `notebooklm auth check --test` runs before any source add. Cheap, purpose-built, fails fast on dead cookies.
2. **Persistent retry queue** at `<vault>/.vault-intake/nlm_queue/<sha1>.json`. When precheck fails or runtime returns an auth-error pattern (`Unauthorized`, `redirect to login`, `CSRF token (missing|expired)`, `SNlM0e not found`, `auth failed/expired/required`), the pending action is serialized as JSON. A separate `flush_nlm_queue()` library function drains the queue once the user runs `notebooklm login`. Non-auth failures (timeout, JSON parse error, source-count exhausted) are NOT queued because re-auth would not recover them.

**Pipeline:**

1. If `config.skip_notebooklm` is True, return `skipped`.
2. Look up `classification.primary` in `config.notebook_map`; if missing, return `skipped`.
3. If `note_path is None` (dry-run), return `skipped`.
4. Run `notebooklm auth check --test`. If it returns nonzero, write a queue entry and return `failed=True, queued=True`.
5. Run `notebooklm source list -n <id> --json` to count sources. If >= 50, return `failed` (no queue: this is not auth-recoverable). If >= 45, set `source_count_warning=True`.
6. Run `notebooklm source add <path> -n <id> --json`. Parse the returned source ID (defensive parser tries top-level `id`, then `source_id`, then nested `source.{id,source_id}`).
7. On any subprocess timeout / JSON parse error / unexpected exception: return `failed` without queue.
8. On nonzero exit with auth-error stderr/stdout: write queue entry and return `failed=True, queued=True`.
9. On nonzero exit with non-auth stderr/stdout: return `failed=True, queued=False`.

**Result shape:** Frozen `NotebookLMResult` with seven fields:

- `source_id: str | None`: set on successful add; None when skipped, failed, or queued.
- `notebook_id: str | None`: resolved notebook ID; None when no mapping or `skip_notebooklm=True`.
- `skipped: bool`: True for the no-op paths (config-disabled, missing mapping, dry-run, missing CLI).
- `failed: bool`: True for any non-skipped non-success outcome (precheck-fail, source-count-exhausted, timeout, JSON parse error, runtime CLI error, unexpected exception).
- `queued: bool`: True when the action was written to the retry queue (always implies `failed=True`). False on every other path including non-auth runtime failures.
- `reason: str`: short audit string (e.g., `"added to nb-ops-id"`, `"auth precheck failed: Unauthorized"`, `"source count exhausted: 50/50"`).
- `source_count_warning: bool`: True when source count >= 45 (Standard plan: 50 cap). Non-blocking.

**Queue file shape (versioned):**

```json
{
  "schema_version": 1,
  "queued_at": "2026-04-30T...",
  "note_path": "/abs/path/to/note.md",
  "notebook_id": "nb-ops-id",
  "classification_primary": "ops",
  "retry_count": 0
}
```

Dedup key is `(notebook_id, note_path)`; the filename is `sha1(notebook_id + "|" + note_path).hexdigest()`. Re-queuing the same `(notebook_id, note_path)` increments `retry_count` rather than creating a duplicate file.

**`flush_nlm_queue` contract:**

- Reads queue files from `<vault>/.vault-intake/nlm_queue/`.
- Drops corrupt files, missing-note-file entries, and entries with the wrong `schema_version`.
- Runs `auth check --test` once upfront. If auth still fails, returns immediately with all valid entries counted as `still_queued`.
- For each remaining entry, attempts `source add`. On success: deletes the queue file. On failure: increments `retry_count` and rewrites.
- Returns `FlushResult(processed, still_queued, dropped)`.

The orchestrator wires the end-of-run UX surface for queued items (single line: "N items queued for NotebookLM; run `notebooklm login` then `vault-intake flush-nlm` to drain") via `IntakeRun.summary()`. Auto-drain runs at the start of every `run_intake` call; `flush_nlm_queue.still_queued` plus this run's queued count populate `IntakeRun.queued_nlm_count`.

**CLI invocation contract:**

All calls use `subprocess.run` with `check=False`, `capture_output=True`, `text=True`, `timeout=30`, `env={"PYTHONIOENCODING": "utf-8", ...}` per the Windows gotcha that Rich emits Unicode that breaks the default Windows codepage.

| Subcommand | Command line |
|---|---|
| Auth precheck | `notebooklm auth check --test` |
| Source count | `notebooklm source list -n <id> --json` |
| Source add | `notebooklm source add <path> -n <id> --json` |

Markdown notes auto-detect as type `text` (content extracted inline). The defensive JSON parser handles both bare-list and `{"sources": [...]}` shapes for list, and tries `id`, `source_id`, and nested `source.{id,source_id}` for add. The CLI's exact response shape for file-uploaded sources is not authoritatively documented; the parser tolerates either form.

The Python module `vault_intake.notebooklm` exposes `NotebookLMResult`, `FlushResult`, `integrate_notebooklm`, and `flush_nlm_queue`:

```python
from vault_intake.notebooklm import integrate_notebooklm, flush_nlm_queue

result = integrate_notebooklm(
    classification=classification,
    frontmatter=frontmatter,         # accepted for parity; not mutated
    config=config,
    note_path=note_path,             # absolute Path, or None for dry-run
    nlm_command="notebooklm",        # injectable for testing
)
result.source_id                     # str | None
result.notebook_id                   # str | None
result.skipped                       # bool
result.failed                        # bool
result.queued                        # bool
result.reason                        # short audit string
result.source_count_warning          # bool

# Drain the persisted queue after the user re-authenticates:
flush = flush_nlm_queue(config)
flush.processed                      # int: queue entries successfully drained
flush.still_queued                   # int: entries that failed again
flush.dropped                        # int: corrupt files or missing notes
```

`NotebookLMResult` and `FlushResult` are frozen. The function returns a result; the orchestrator owns updates to `frontmatter.source_id` (via `dataclasses.replace`) when the result carries a non-None `source_id`.

**v1 deliberate simplifications:**

- The CLI's exact JSON response shape is not authoritatively documented for file-uploaded sources. The parser is defensive over likely shapes; if a future CLI version changes the shape further, the parser falls back to `JSONDecodeError` and the path returns `failed` (no queue, since this is not auth-recoverable).
- Source-count warning threshold is hardcoded at 45 (Standard plan: 50 cap). A config knob is deferred until a Pro plan use case surfaces.
- Retry-count cap is not enforced in v1; queued entries persist until drained. A v2 cap (e.g., drop after 5 retries) is deferred until dogfood reveals demand.
- The orchestrator's end-of-run "N items queued" UX surface is not part of Step 9; it is owned by the orchestrator's `IntakeRun.summary()` and is wired up via auto-drain at run start (see Orchestrator section below).

## Orchestrator (two entrypoints)

The orchestrator wires Steps 0-9 into the spec's output contract per build spec lines 228-243 across two entrypoints. `run_intake` is the dry-run pass that produces an `IntakeRun` with the assembled markdown body, the proposed destination, the questions tuple, and the queue surface, without touching the filesystem. `confirm_and_write` is the post-confirmation pass that performs the atomic file write, re-invokes Step 9 live against the written path, threads any returned `source_id` back into frontmatter, and atomically re-writes the file. Section-update mode (context+project routes to `projects/{slug}.md`) appends a section to the existing project hub and skips live Step 9 to avoid duplicate NotebookLM sources.

Pipeline ordering (locked 2026-04-30):

1. Auto-drain the NotebookLM retry queue (best-effort; `flush_nlm_queue.still_queued` contributes to `IntakeRun.queued_nlm_count`).
2. Step 1: detect content type.
3. Step 2: refine, gated on `config.refinement_enabled` AND `detection.refinement_applicable`.
4. Step 3: classify, wraps NotImplementedError so emergent-mode runs degrade gracefully.
5. Step 4: PARA, skipped in emergent mode; wraps NotImplementedError.
6. Step 5: generate frontmatter, wraps NotImplementedError; needs a non-None `ParaResult` in fixed_domains.
7. Step 6: generate wikilinks, wraps NotImplementedError; needs classification plus para in fixed_domains.
8. Step 7: extract next-actions, mode-agnostic and content-driven; runs regardless of upstream skips.
9. Step 8: route, needs classification plus frontmatter; needs para in fixed_domains.
10. Step 9: NotebookLM. In `run_intake` (dry-run) always called with `note_path=None` and returns a `skipped` result. In `confirm_and_write` (post-confirmation) called with the written file path so the live add path runs; section-update mode skips live Step 9 entirely.

The orchestrator owns ALL `Frontmatter` mutations via `dataclasses.replace`; library functions never mutate inputs. When `confirm_and_write`'s live Step 9 returns a non-None `source_id`, the orchestrator threads it back into `frontmatter.source_id`, re-renders final markdown via the YAML-block swap, and re-writes the file atomically.

Final markdown layout (kickoff item 4):

```
---
{frontmatter.to_yaml()}
---

{refined_or_original_body}

## Possíveis próximos passos      # only when next_actions.gate_fired
{next_actions.to_markdown()}

## Captura original                # only when refinement.changed
{refinement.original}
```

Wikilink proposals are NOT auto-appended; they are surfaced in `IntakeRun.wikilinks` for user confirmation per safety rule 5.

The Python module `vault_intake.orchestrator` exposes `IntakeRun`, `assemble_final_markdown`, `collect_questions`, `run_intake`, and `confirm_and_write`:

```python
from vault_intake.orchestrator import run_intake

result = run_intake(
    input_text=text,
    config=config,
    source_type="paste",      # default
    source_uri="",            # default
    captured_at=None,         # default: today's ISO date
    nlm_command="notebooklm", # injectable for testing
)
result.detection              # DetectionResult
result.refinement             # RefinedContent | None (None when Step 2 skipped)
result.classification         # ClassificationResult | None (None when Step 3 not implemented)
result.para                   # ParaResult | None (None in emergent or Step 4 not implemented)
result.frontmatter            # Frontmatter | None (None when Step 5 not implemented or upstream missing)
result.wikilinks              # WikilinkResult | None
result.next_actions           # NextActionsResult (always present; Step 7 mode-agnostic)
result.route                  # RouteResult | None
result.notebooklm             # NotebookLMResult | None (always skipped under run_intake)
result.body                   # post-refinement body text (or raw input when refinement skipped)
result.final_markdown         # assembled body; "" when frontmatter is None
result.written_path           # None under run_intake; set by confirm_and_write
result.queued_nlm_count       # still_queued from auto-drain plus this run's queued count
result.questions              # tuple[str, ...] of confirmation prompts
result.summary()              # spec output contract per build spec lines 228-243
```

`confirm_and_write` accepts the dry-run `IntakeRun` and produces a new `IntakeRun` with `written_path` set, the live Step 9 result, and (when the live result carried a `source_id`) a frontmatter mutated via `dataclasses.replace`:

```python
from vault_intake.orchestrator import confirm_and_write

written = confirm_and_write(
    result,                           # IntakeRun from run_intake
    config,
    nlm_command="notebooklm",          # injectable for testing
    overwrite=False,                   # raise FileExistsError on collision unless True
)
written.written_path                   # Path of the file that was written
written.notebooklm                     # live NotebookLMResult from the post-write Step 9
written.frontmatter.source_id          # populated when live Step 9 succeeded
written.queued_nlm_count               # carry-forward + 1 if live result.queued
```

Behavior contracts:

- Regular write: `{frontmatter.title}.md` placed at `route.destination` (a folder). Atomic via temp file plus `os.replace`. Raises `FileExistsError` when the target exists unless `overwrite=True`.
- Section update (`route.is_section_update == True`): `route.destination` IS the file path. Appends a `## {title}` section plus optional `## Captura original` block. Raises `FileNotFoundError` when the destination does not exist. Live Step 9 is skipped.
- Defense in depth (spec safety rule 6): the destination must be inside `config.vault_path`. Raises `ValueError` otherwise.

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
