# vault-intake

Trusted capture skill for Second-Brain vaults. Memory Branch M2 (v0.2.0) of the Agent OS project.

## What it does

Reads source content (notes, transcripts, articles, links), extracts and structures it into Markdown notes with frontmatter, suggests PARA folder placement and wikilinks, and writes to your local Obsidian vault.

Designed to run as a Claude Code skill. Standalone CLI invocation also supported via `scripts/intake.py`.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code](https://claude.com/claude-code) Pro or Max subscription
- An Obsidian vault directory with a `CLAUDE.md` containing a `## Vault Config` block
- Optional: NotebookLM CLI for syncing captured notes to a notebook

## Install

```bash
git clone https://github.com/jgerton/vault-intake.git
cd vault-intake
uv sync
uv run scripts/install_skill.py
```

You'll need a vault directory with a `CLAUDE.md` containing a `## Vault Config` block. See [`SKILL.md`](./SKILL.md) for the full reference, required fields, and CLI flags.

Currently in design-partner pilot phase. For setup help during alpha, contact jgerton@gmail.com.

## Where is my stuff?

After running `bootstrap_vault()` against a configured vault, you'll have this layout:

| Path | Purpose |
|---|---|
| `<vault>/` | Vault root. Lives wherever you point `vault_path` in `CLAUDE.md`. |
| `<vault>/CLAUDE.md` | Per-vault config. The `## Vault Config` block is the source of truth. |
| `<vault>/inbox/` | Drop new `.md` files here for `--inbox` batch processing. |
| `<vault>/_inbox/` | System fallback when classification is uncertain in emergent mode. |
| `<vault>/<domain>/sessions/` | Where confirmed notes land in fixed_domains mode (one folder per configured domain). |
| `<vault>/projects/` | PARA project hub notes. |
| `<vault>/insights/`, `workflows/`, `prompts/`, `context/`, `references/` | Other PARA folders for fixed_domains routing. |
| `<vault>/.vault-intake/inbox-processed/` | `--inbox` archive: source files moved here after successful processing. |
| `<vault>/.vault-intake/nlm_queue/` | NotebookLM retry queue. Drained via `scripts/flush_nlm.py`. |

The vault is a regular folder. There is no central registry, no install-wide state file, no hidden config outside the vault itself. Move it, back it up, or delete it like any other directory.

## Multiple vaults

Each vault has its own `CLAUDE.md`. To run intake against a different vault, pass `--vault PATH` (or set `VAULT_INTAKE_VAULT_PATH`). Nothing about vault-intake is shared across vaults: each one's classification mode, configured domains, NotebookLM mappings, and routing live entirely inside its own `CLAUDE.md`.

```bash
# Vault A
uv run scripts/intake.py --vault /path/to/personal-vault --inbox

# Vault B (different config, different domains, different NotebookLM mapping)
uv run scripts/intake.py --vault /path/to/work-vault --inbox
```

The vault path can be local, on a network share, on a mapped drive, or inside a synced cloud folder (Dropbox, OneDrive, iCloud Drive). vault-intake doesn't care; it's just filesystem I/O.

## Multi-account NotebookLM

vault-intake calls the [`notebooklm-py`](https://pypi.org/project/notebooklm/) CLI for Step 9 (sync to a NotebookLM notebook). That CLI uses Google session cookies and is **single-account at a time**. Switching accounts requires re-authenticating via `notebooklm login`. There is currently no clean way to run intake against multiple NotebookLM accounts in parallel from the same machine.

If you have personal, work, and team NotebookLM accounts and want them all integrated, the practical options today are:

- Pick one account per vault and re-auth when switching contexts
- Skip NotebookLM integration for vaults whose notebooks live under a different account (`skip_notebooklm: true` in `CLAUDE.md`)
- Disable Step 9 per-run (`--skip-notebooklm` flag) when you're working in a context that doesn't match the currently-authenticated account

Honest acknowledgement: this is a real limitation. It's an upstream CLI constraint, not a vault-intake choice.

## Moving your vault

The vault is a folder. To move it:

1. Move the folder (`mv`, `cp -r`, or your file manager). Network share or cloud target works the same way.
2. Update `vault_path` in the moved vault's `CLAUDE.md` to the new absolute path.
3. Done. There is nothing else to update.

If you have automation pointing at the old vault path (cron entries, shell aliases, the `VAULT_INTAKE_VAULT_PATH` env var), update those too.

## Status

Memory Branch M2 (v0.2.0). Active design-partner pilot. Not yet recommended for general production use.

## License

vault-intake is released under the [GNU Affero General Public License v3.0](./LICENSE).

You can use it freely for personal, internal business, and research purposes. If you distribute it as a network service, your service must also be released under AGPL-3.0.

For commercial use needing different terms, contact jgerton@gmail.com.
