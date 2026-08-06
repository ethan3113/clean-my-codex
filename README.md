# Clean My Codex

Clean My Codex is a macOS utility for reviewing Codex chats, repairing moved workspace paths, and removing unwanted Codex records through a reversible Trash Bin.

Every destructive workflow follows the same model:

```text
Preview -> Confirm -> Trash Bin -> Restore or Permanently Delete
```

Version `0.1.0`  
Created by [ENVOCS Studio](https://github.com/ethan3113)

> Clean My Codex is an independent open-source project. It is not affiliated with, endorsed by, or supported by OpenAI.

## Requirements

- macOS
- Python 3.10 or newer
- Codex with a data directory at `~/.codex`

No package installation is required. The app uses the Python standard library and bundled static assets.

## Start

After cloning the repository, either double-click `Clean My Codex.command` or run:

```bash
cd clean-my-codex
./run.command
```

The launcher opens a protected session at `http://127.0.0.1:8765`. Keep the Terminal window open while using it. Open the app through the launcher rather than a saved browser URL; each launch uses a new session capability.

To choose another Codex data directory or port while keeping the protected launcher flow:

```bash
CLEAN_MY_CODEX_HOME="$HOME/.codex" \
CLEAN_MY_CODEX_PORT=8765 \
./run.command
```

## What It Does

- **Chat Manager** reviews chats and moves selected records to Trash Bin.
- **Missing Paths** finds unavailable workspace paths and offers relink or cleanup.
- **Relocation Manager** replaces an old workspace path with a new one after preview.
- **Folder Cleaner** classifies stale and backup-looking files conservatively.
- **Trash Bin** restores removed records or permanently removes Trash Bin items.
- **Operation Logs** records applied actions and verification results.

Deleting a project reference never deletes the actual project directory from Documents or any other workspace location.

## Safety Model

- Preview is the default for every change.
- Active `~/.codex` files are never permanently deleted directly.
- Related session files and metadata snapshots enter `trash-bin/` first.
- SQLite updates use targeted transactions, exported rows, and integrity checks.
- Unknown references are skipped and marked for review.
- The server binds only to loopback and protects every data API with a per-launch capability that is not returned by the health endpoint.
- Trash restore fingerprints active metadata and blocks automatic restore if newer Codex activity changed any protected file.
- `auth.json`, runtime databases, session logs, backups, and generated reports are excluded from public releases.

Read [SAFETY_AND_BACKUP_POLICY.md](SAFETY_AND_BACKUP_POLICY.md) and [TRASH_BIN_SYSTEM.md](TRASH_BIN_SYSTEM.md) before applying changes to important Codex data.

## Common Workflows

### Delete a chat

1. Open **Chat Manager** and select one or more chats.
2. Choose **Preview** and review every matched file and metadata reference.
3. Choose **Delete** and type `DELETE CHAT`.
4. Check the verification result and Trash Bin item.

### Repair a moved project

1. Open **Relocation Manager**.
2. Enter the old and new paths.
3. Preview the exact references that will change.
4. Apply only after verifying the destination is correct.

### Restore or permanently delete

Open **Trash Bin**, select an item, and inspect its restore data.

- Restore requires `RESTORE FROM TRASH`.
- Permanent deletion requires `PERMANENT DELETE` and affects only the selected Trash Bin item.

## Development

Run the complete verification suite:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile clean_my_codex/*.py scripts/*.py
node --check static/app.js
python3 scripts/build_release.py --output-dir release --replace
```

The release builder copies only files listed in `PUBLIC_RELEASE_FILES.txt`. It rejects symbolic links, unsafe versions, runtime folders, database and session formats, machine paths, email addresses, UUID-like session identifiers, and common credential patterns before creating the archive.

For published releases, verify the ZIP checksum and confirm that the GitHub release points to the expected version tag. A checksum downloaded beside an archive detects transfer errors; the tag identifies the source commit the project published.

## Report an Issue

Use the bug button in the lower-right corner of the app or open the [Clean My Codex issue form](https://github.com/ethan3113/clean-my-codex/issues/new?template=bug_report.yml). Redact chat content, credentials, and identifying paths from screenshots or logs.

Security reports should follow [SECURITY.md](SECURITY.md).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests should stay narrowly scoped, preserve the preview-first safety model, and include tests for changes that can affect Codex data.

## License

Clean My Codex is released under the [MIT License](LICENSE). Bundled icon notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
