# Safety And Backup Policy

## Core Rule

Never permanently delete directly from:

```text
~/.codex
```

All cleanup actions must first create a restorable Trash Bin item under:

```text
<app-folder>/trash-bin
```

Permanent delete is allowed only inside `trash-bin`.

An apply request is accepted only after a fresh preview. Its one-time receipt is bound to the exact action, selected targets, and current metadata state, and expires after ten minutes.

## Trash Bin Contents

Each new delete operation creates:

```text
trash-bin/<timestamp>-<type>-<short-id>/
```

Each item includes:

- `manifest.json`
- `RESTORE_REPORT.md`
- moved session or stale files under `moved/`
- metadata snapshots under `metadata-snapshots/`
- SQLite row export in `sqlite_rows.json`
- removed `session_index.jsonl` lines
- removed `.codex-global-state.json` reference paths
- original source-to-trash path mappings

## Confirmations

| Action | Confirmation |
|---|---|
| Delete chat | `DELETE CHAT` |
| Move missing paths to Trash Bin | `MOVE MISSING PATHS TO TRASH` |
| Delete project from Codex | `DELETE PROJECT FROM CODEX` |
| Move stale Codex files to Trash Bin | `MOVE CODEX FILES TO TRASH` |
| Restore from Trash Bin | `RESTORE FROM TRASH` |
| Permanently delete Trash Bin item | `PERMANENT DELETE` |
| Apply relocation | `APPLY RELOCATION` |

## SQLite Safety

- Inspect schema dynamically.
- Delete only rows clearly linked to selected thread IDs or rollout paths.
- Use transactions.
- Run `PRAGMA integrity_check`.
- Export affected rows before deletion.
- If a table has thread-looking columns that are not understood, skip that table and mark manual review.

## Project Delete Safety

Deleting a project from Codex removes Codex references and all related Codex chats for that path.

It does not delete the actual project folder from `Documents`.

## Restore Safety

Restore uses the Trash Bin item only. It restores moved files, exact exported SQLite rows, and guarded text metadata snapshots. If original files already exist, restore refuses overwrite unless the caller explicitly allows overwrite.

Each delete records post-delete fingerprints for protected metadata. Restore compares current files with those fingerprints and automatically blocks metadata replacement if Codex activity changed them. A safe restore first creates a restore guard inside the selected Trash Bin item.

SQLite fingerprints include the active database state, including uncheckpointed write-ahead-log changes. Restore destinations are rejected when any path component is a symbolic link.

Failed operations roll back only changes whose current fingerprint still matches the value written by that operation. Newer external changes are never replaced by an old snapshot; the item is marked `failed-manual-restore-needed` instead.

Trash items created by older releases do not have post-delete fingerprints and therefore require manual review instead of automatic metadata restore.

## Legacy Folders

Older folders may still exist for compatibility:

- `backups`
- `trash`
- `purge-backups`
- `codex-archive`

New cleanup operations should use only `trash-bin`.
