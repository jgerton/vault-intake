# vault-intake setup guide

> Written for early pilot users getting hands-on with the M1 build. About 30 to 45 minutes from clone to first capture.

## Welcome

vault-intake is a Claude Code skill that captures content (notes, transcripts, articles, links) into your local Obsidian vault as structured Markdown notes. It assigns a content type, classifies the content into one of your domains, picks a PARA folder, drafts frontmatter and suggested wikilinks, and (optionally) syncs the note to a NotebookLM notebook as a source.

By the end of this guide, you'll have it installed and you'll have done your first capture into a test vault. From there, you can point it at your real workflow.

### Important framing before we start

vault-intake ships in two modes: `fixed_domains` (works today) and `emergent` (not yet implemented; Steps 3-6 raise `NotImplementedError`). For this install we use `fixed_domains` as the stand-in.

If your mental model leans more toward "themes emerge from the content I capture" rather than "I pre-define domains and content slots into them," you'll feel friction in this mode. **That friction is the design signal we want.** Capture it as you go (a one-line note in Skool DM is enough) and we'll fold it into the spec for emergent mode. Your confusion is the requirement.

---

## Prerequisites

Before you start, make sure you have:

| Requirement | Where | Notes |
|---|---|---|
| Python 3.12+ | https://www.python.org/downloads/ | Check with `python --version` |
| uv | https://docs.astral.sh/uv/getting-started/installation/ | Modern Python package manager; replaces pip plus venv |
| Git | https://git-scm.com/downloads | Standard install |
| Claude Code (Pro or Max) | https://claude.com/claude-code | Pro ($20/mo) is the minimum; Max 5x ($100/mo) recommended for daily heavy use |
| An Obsidian vault directory | Anywhere on your filesystem | The vault doesn't need to be open in Obsidian; vault-intake just writes Markdown files into it |
| NotebookLM CLI (optional) | Skip for now | We set `skip_notebooklm: true` for your first install |

**One quick question to DM back about:** which Claude Code tier are you on right now (Pro or Max)? Light pilot use is fine on Pro; heavy daily capture leans toward Max. Knowing your tier helps me calibrate what to expect.

---

## Step 1: Install vault-intake

```bash
# Clone the repo to wherever you keep code projects
git clone https://github.com/jgerton/vault-intake.git
cd vault-intake

# Install dependencies into a project-local venv via uv
uv sync

# Install the skill into Claude Code
uv run scripts/install_skill.py
```

You should see output similar to:

```
installed: <N> files, 2 dirs synced, dest=/home/you/.claude/skills/vault-intake
```

The skill is now installed at `~/.claude/skills/vault-intake/`. Open a fresh Claude Code session and `/vault-intake` will be available.

**Common install hiccups:**
- `uv: command not found`: install uv first per the link in the prereqs table.
- `ImportError` or `ModuleNotFoundError`: run `uv sync` again from the repo root.
- Install runs but `/vault-intake` doesn't appear in Claude Code: open a NEW Claude Code session. The skill loads at session start, so existing sessions won't see it.

---

## Step 2: Pick or create your vault

vault-intake reads from and writes to a directory you designate as your vault. Most users point it at their existing Obsidian vault. For the first run, a fresh test vault is easier:

```bash
# Example: a clean test vault to experiment with
mkdir ~/test-vault
cd ~/test-vault
mkdir -p projects areas resources archive
```

The four subdirectories match the PARA structure (`projects`, `areas`, `resources`, `archive`). vault-intake will route captured notes into these based on classification.

---

## Step 3: Configure your vault

vault-intake reads its configuration from a `## Vault Config` block inside your vault's `CLAUDE.md` file. Create that file at the root of your vault:

```bash
# from your vault root
touch CLAUDE.md
```

Open `CLAUDE.md` in your editor and add this content (adjust `vault_path` and `domains` to fit you):

````markdown
# My vault

## Vault Config

```yaml
vault_path: /absolute/path/to/your/vault
classification_mode: fixed_domains
routing_mode: para
language: pt-BR
skip_notebooklm: true
refinement_enabled: true
domains:
  - slug: brand
    description: Visual identity, branding, design system work
  - slug: client
    description: Client engagements and project notes
  - slug: process
    description: Workflows, templates, recurring patterns
  - slug: reference
    description: Reading, research, external sources
```
````

**Key fields:**

- `vault_path`: absolute path to your vault directory (the same directory holding this `CLAUDE.md`).
- `classification_mode: fixed_domains` plus `routing_mode: para`: this is the only mode shipping in M1. The emergent mode pair is reserved for the next milestone.
- `language`: `pt-BR` for Portuguese, `en` for English, or another ISO code. Affects content refinement and next-action phrasing.
- `skip_notebooklm: true`: skips Step 9 (NotebookLM source sync). Flip to `false` later when you set up NotebookLM CLI.
- `refinement_enabled: true`: runs Step 2 (readability pass) on oral or brain-dump content. Set to `false` if you prefer raw text.
- `domains`: your customizable categorization scheme. **The slugs above are examples**. Replace them with names that match how YOU naturally categorize content. fixed_domains mode requires a non-empty domains list.

---

## Step 4: Capture your first content

Two ways to invoke the skill.

### Option A: Inside Claude Code (recommended)

Open Claude Code from your terminal and invoke the skill:

```
/vault-intake
```

The skill loads. Tell it what to capture in plain language. It runs the pipeline (classify, refine, classify into your domains, pick PARA folder, draft frontmatter, suggest wikilinks, propose next-actions, suggest destination), shows you a preview, and asks for confirmation before writing.

### Option B: Direct CLI

```bash
# Pipe content from stdin
echo "Some content to capture" | uv run scripts/intake.py --vault /path/to/your/vault

# Or read from a file
uv run scripts/intake.py --vault /path/to/your/vault --input /path/to/content.md
```

Useful flags:
- `--source-type {vault,paste,stdin,api,external_cli,other}`: where the content came from
- `--source-uri TEXT`: a URL or origin reference (added to frontmatter)
- `--title TEXT`: override the auto-generated title
- `--dry-run`: run the pipeline, print the proposed output, write nothing
- `--yes`: skip the confirmation prompt (use only after you trust the previews)
- `--skip-notebooklm`: bypass Step 9 for this run regardless of config

Exit codes: `0` success, `1` user aborted, `2` config error, `3` pipeline error, `4` file write error.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `config error: vault_path required` | Missing `vault_path` field, or the `## Vault Config` block isn't a fenced YAML code block | Compare against the example in Step 3 line by line; YAML inside a triple-backtick `yaml` fence under a `## Vault Config` heading |
| `config error: fixed_domains mode requires non-empty 'domains' list` | You set `classification_mode: fixed_domains` but defined no domains | Add at least one domain entry under `domains:` |
| `unsupported (classification_mode, routing_mode) pair` | Mode pair doesn't match either valid combo | Use `(fixed_domains, para)` for now; `(emergent, emergent)` is not implemented |
| `/vault-intake` not in Claude Code | Existing session, or install didn't run | Run `uv run scripts/install_skill.py`, then open a NEW Claude Code session |
| `ImportError` or `ModuleNotFoundError` | venv not set up | Run `uv sync` from the repo root |
| NotebookLM auth errors despite `skip_notebooklm: true` | Bug; should not happen | Copy the error and DM me; I'll investigate |

---

## What to do next

Once you've done a few captures and gotten a feel for what works and what feels off:

1. **DM me on Skool** with what you saw. One-line observations are valuable. Examples worth flagging:
   - Domain classifications that felt forced ("this content didn't fit any of my domains")
   - PARA assignments that didn't match your mental model
   - Frontmatter fields that were missing or wrong
   - Wikilink suggestions that hit or missed
   - Anything where you wished it asked you something instead of guessing
2. **Try it on real content** from your normal flow. Demo runs help verify install; real use generates the design signal we need.
3. **Watch for the emergent-mode signal**: every place fixed_domains forced you to fit content into a domain that didn't quite match is a gap we'll address in the next milestone.

**If you get stuck or want to discuss anything, just DM me on Skool.** No calls, no scheduling overhead, async whenever you have a moment.

---

## Reference

- [`SKILL.md`](./SKILL.md): full skill description, pipeline architecture, and library API documentation
- [`scripts/intake.py`](./scripts/intake.py): direct CLI implementation and all flag definitions
- [`scripts/install_skill.py`](./scripts/install_skill.py): install script (allowlist, containment, idempotency notes)
- [`LICENSE`](./LICENSE): AGPL-3.0 full text. Free for personal, internal business, and research use. For commercial use needing different terms, contact jgerton@gmail.com.
