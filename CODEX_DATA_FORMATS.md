# Codex Data Formats

Clean My Codex treats Codex storage as an evolving implementation detail. It discovers supported files and SQLite schema at runtime and skips structures it cannot identify safely.

## Supported Data Categories

- Session rollout files under `~/.codex/sessions/`.
- Session titles and references in `session_index.jsonl` when present.
- Thread rows in recognized `state_*.sqlite` databases.
- Exact thread and workspace references in `.codex-global-state.json`.
- Project trust entries in `config.toml` for relocation preview and apply.
- Backup-looking and temporary files identified by conservative filename rules.

## Files Never Read as Chat Data

- `auth.json`
- Credential files
- Browser state
- Plugin caches
- Unknown binary formats

## Modification Rules

- JSON and JSONL changes use exact matching and atomic replacement.
- SQLite changes inspect schema first, export matched rows, use a transaction, and run `PRAGMA integrity_check`.
- Session files are moved to Trash Bin rather than hard-deleted.
- Unknown relationships are skipped.
- Restore refuses an overwrite unless it is explicitly confirmed.

The actual structures may change between Codex releases. Review every preview before applying an operation after a Codex update.
