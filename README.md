# vault-intake

Trusted capture skill for Second-Brain vaults. Memory Branch M1 of the Agent OS project.

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

## Status

Memory Branch M1, alpha. Active design-partner pilot. Not yet recommended for general production use.

## License

vault-intake is released under the [GNU Affero General Public License v3.0](./LICENSE).

You can use it freely for personal, internal business, and research purposes. If you distribute it as a network service, your service must also be released under AGPL-3.0.

For commercial use needing different terms, contact jgerton@gmail.com.
