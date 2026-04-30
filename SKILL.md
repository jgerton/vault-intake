---
name: vault-intake
description: Memory Branch M1 work-in-progress. Step 0 (Bootstrap: config resolve and validate) and pipeline Steps 1 (Detect content type), 2 (Refine), 3 (Classify, fixed_domains mode only), 4 (PARA category, fixed_domains/para mode only), 5 (Generate frontmatter, fixed_domains track only), and 6 (Generate wikilinks, fixed_domains track only) are implemented. Step 0 parses a Second-Brain vault's CLAUDE.md `## Vault Config` YAML block, enforces the Option Z mode pair lock, and returns resolved JSON. Step 1 classifies raw input into one of seven closed-enum content types and surfaces an uncertainty flag when signals overlap. Step 2 produces a readability-pass refinement of oral or brain-dump content while preserving the verbatim original. Step 3 classifies fixed_domains-mode content into a primary domain plus secondary tags using rule-based keyword matching, with a configurable confidence threshold and an uncertainty flag for caller-driven confirmation. Step 4 categorizes content into one of four PARA buckets (project, area, resource, archive) using rule-based heuristics over the project inventory under `vault_path/projects/`, the upstream detection result, and superseded-decision phrasing; emergent mode skips PARA entirely and raises NotImplementedError on direct call. Step 5 builds a frozen `Frontmatter` dataclass populated from the upstream pipeline outputs plus capture metadata, emitting the OS-wide canonical baseline (architecture plan Section 1.4.1) and the fixed_domains track-specific additions (build spec lines 122-135) with a kebab-case title heuristic, capped tags, and a `to_yaml()` serializer; emergent mode raises NotImplementedError. Step 6 walks the vault, parses each markdown file's frontmatter, and produces ranked wikilink proposals (cross-domain weight 4, active project weight 3, concept overlap weight 2 at a 2-token floor, empty backlog markers from typed `[[X]]` weight 1) capped at 7 with dedupe by target and recency-then-alphabetical tiebreaks; emergent mode raises NotImplementedError. Use this skill when the user asks to "validate vault config," "check vault CLAUDE.md," "resolve vault-intake config," "detect vault-intake content type," "refine vault-intake content," "classify vault-intake content," "categorize vault-intake PARA," "generate vault-intake frontmatter," or "generate vault-intake wikilinks" against specific input. Do not use this skill for general capture, intake, or routing tasks; the spec's pipeline Steps 7 through 9 (next-actions, route, NotebookLM) are not yet implemented and will land in subsequent commits.
---

# vault-intake

Memory Branch Milestone 1 (M1) skill, in progress. The full design is a universal capture skill for Second-Brain vaults. The spec's pipeline runs Steps 1 through 9; Step 0 (Bootstrap: config resolve and validate) is a precondition implemented as part of this skill, not part of the numbered pipeline. Step 0 and pipeline Steps 1, 2, 3 (Classify, fixed_domains mode only), 4 (PARA category, fixed_domains/para mode only), 5 (Generate frontmatter, fixed_domains track only), and 6 (Generate wikilinks, fixed_domains track only) are implemented and usable; Steps 7 through 9 remain. Emergent-mode classification, emergent routing, the emergent frontmatter shape, and emergent-mode wikilinks are parallel tracks that land in separate sessions once fixed_domains stabilizes.

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
| 7. Extract candidate next-actions | Not implemented |
| 8. Route to destination folder | Not implemented |
| 9. NotebookLM integration | Not implemented |

Do not invoke this skill end-to-end against a real vault. Only the Step 0 (Bootstrap), Step 1 (Detect content type), Step 2 (Refine), Step 3 (Classify, fixed_domains), Step 4 (PARA, fixed_domains/para), Step 5 (Generate frontmatter, fixed_domains), and Step 6 (Generate wikilinks, fixed_domains) helpers are safe to use today; all seven produce intermediate output rather than vault writes.

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

## Pipeline (Steps 7 through 9, planned)

Documented for reference; not implemented yet. Each will land in subsequent commits with its own tests. Steps 1 through 6 are described in their own sections above.

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
