# Trash Bin System

## Mental Model

```text
Delete -> Move to Trash Bin -> Restore or Permanently Delete
```

There is one active cleanup destination:

```text
<app-folder>/trash-bin
```

## What Delete Means

Delete never means immediate permanent deletion from `~/.codex`.

Delete means:

- move related files into `trash-bin`
- save metadata snapshots
- export affected SQLite rows
- remove proven active references from `.codex`
- verify cleanup
- leave a restorable Trash Bin item

Preview and apply are paired. Apply consumes a one-time receipt for the exact targets and is rejected if the selection or active Codex metadata changed after preview.

## Trash Item Layout

```text
trash-bin/<timestamp>-<type>-<short-id>/
  manifest.json
  RESTORE_REPORT.md
  sqlite_rows.json
  moved/
  metadata-snapshots/
```

Supported item types:

- `chat`
- `project`
- `missing-path`
- `stale-file`

## Chat Delete

Moves selected chat session files into Trash Bin and removes exact active references from:

- `state_5.sqlite`
- `sqlite/state_5.sqlite`
- `session_index.jsonl`
- `.codex-global-state.json`

Confirmation:

```text
DELETE CHAT
```

## Missing Path Delete

Moves all chats related to a missing path into Trash Bin and removes exact path/thread references.

Confirmation:

```text
MOVE MISSING PATHS TO TRASH
```

## Project Delete

Deletes only Codex project/workspace references and related Codex chats.

It does not delete the real project folder from `Documents`.

Confirmation:

```text
DELETE PROJECT FROM CODEX
```

## Folder Cleaner Delete

Safe stale/backup-looking `.codex` files can be moved into Trash Bin.

Confirmation:

```text
MOVE CODEX FILES TO TRASH
```

## Restore

Restore uses the Trash Bin item to restore moved files, exact exported SQLite rows, and guarded text metadata snapshots.

Automatic metadata restore runs only when the active files still match their post-delete fingerprints, including current SQLite write-ahead-log state. Changed or older unversioned Trash items stop for manual review so newer unrelated records are not overwritten.

If an operation fails, automatic rollback touches only fingerprints produced by that operation. A concurrent Codex change stops rollback and leaves the Trash item plus snapshots available for review.

Confirmation:

```text
RESTORE FROM TRASH
```

## Permanent Delete

Permanent delete removes only the Trash Bin item folder.

Confirmation:

```text
PERMANENT DELETE
```

Permanent delete must never target active `~/.codex` paths.

## Limitations

- Automatic restore is blocked when active metadata changed after deletion or when an older Trash item has no fingerprint.
- Unknown SQLite schema relationships are skipped and marked manual review.
- The app does not delete real project folders from `Documents`.
- Legacy cleanup folders remain readable for compatibility but are no longer the main workflow.
