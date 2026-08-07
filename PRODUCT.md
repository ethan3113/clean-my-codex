# Clean My Codex

Version `0.3.0`, created by [ENVOCS Studio](https://github.com/ethan3113).

## Product

Clean My Codex is an on-device utility for inspecting and safely maintaining Codex workspace data under `~/.codex`.

It helps people find chats, repair moved project paths, inspect stale files, move Codex records into a reversible Trash Bin, and restore them without manually editing internal data.

## Core Promise

Every potentially destructive workflow follows one predictable path:

`Preview -> Confirm -> Move to Trash Bin -> Verify -> Restore or Permanently Delete`

The safe action stays obvious, while technical evidence remains available on demand instead of dominating the interface.

## Operating Model

- Opens as a native macOS Apple Silicon, macOS Intel, or Windows x64 application; the macOS source launcher remains available for development and compatibility.
- Runs on this device at `127.0.0.1` with no hosted service.
- Reads a selected Codex data root, normally `~/.codex`.
- Defaults to dry-run and preview workflows.
- Moves recoverable data and metadata snapshots into Trash Bin before removing proven active references.
- Uses SQLite transactions, row exports, and integrity checks for database changes.
- Never deletes actual project folders from disk.
- Never directly permanently deletes active files from `~/.codex`.
- Skips uncertain references and marks them for review.
- Does not read, copy, or expose credentials and secret-bearing files.

## Primary Workflows

1. Find and inspect Codex chats.
2. Delete a chat into the unified Trash Bin.
3. Detect a missing project path and either relink it or move its Codex records to Trash Bin.
4. Relocate an old project path to a new path with preview and backup.
5. Inspect stale or backup-looking Codex files and move safe candidates to Trash Bin.
6. Restore or permanently delete items already inside Trash Bin.
7. Review operation history and verification results.

## Experience Principles

- Compact, calm, professional, and keyboard-friendly.
- One primary status per item; supporting evidence appears in a contextual inspector.
- One obvious next action per list item; destructive actions use progressive disclosure and typed confirmation.
- Red is reserved for confirmed destructive contexts, not routine navigation or scanning.
- Technical data remains accessible without dominating the default interface.
- Empty, loading, success, partial, and review states are explicit.
- Status meaning uses distinct mini icons as well as color and text.
- The visual system is neutral and high-contrast, inspired by modern developer tools without copying another product's brand.
- Public examples use generic home-relative paths rather than installation-specific directories.

## Public Distribution Boundary

The repository contains reusable source code, generic documentation, and synthetic fixtures only. Runtime data, screenshots, reports, raw session logs, database copies, credentials, and identifying workspace names are excluded by an allowlisted release process.

Generated `.app` bundles are release artifacts, not repository source. Public app downloads must be separately audited, signed, notarized, and attached to the matching release tag.

## Accessibility

- All controls remain usable with a keyboard.
- Focus states are visible.
- Status is never communicated by color alone.
- Text remains legible at compact sizes and reflows without overlap.
- Navigation motion uses a consistent one-second rhythm and is disabled when reduced motion is requested.
- Navigation and selection motion explain the action through icon behavior rather than decorative movement.
