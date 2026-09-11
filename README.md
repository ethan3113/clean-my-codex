# Clean My Codex

Clean My Codex is a desktop utility for reviewing Codex chats, repairing moved workspace paths, and removing unwanted Codex records through a reversible Trash Bin.

Every destructive workflow follows the same model:

```text
Preview -> Confirm -> Trash Bin -> Restore or Permanently Delete
```

Version `0.3.1`
Created by [ENVOCS Studio](https://github.com/ethan3113)

> Clean My Codex is an independent open-source project. It is not affiliated with, endorsed by, or supported by OpenAI.

## Desktop Packages

| Package | Platform | Runtime requirement |
|---|---|---|
| `macOS arm64` | Apple Silicon Mac | Bundled |
| `macOS x86_64` | Intel Mac | Bundled |
| `Windows x64` | Windows 10 1809 or newer | [Microsoft Edge WebView2 Evergreen Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/consumer/) |

All packages expect Codex data in the platform account's `.codex` directory unless `CLEAN_MY_CODEX_HOME` is configured. Python is not required to open a packaged app.

Clean My Codex has no account system. It does not require a Clean My Codex login, OpenAI API key, or separate ChatGPT sign-in. Each installation reads the compatible Codex data directory belonging to the signed-in operating-system account; it is not tied to the developer's Codex data or credentials.

## Open the macOS App

Choose the package matching the Mac processor, then open `Clean My Codex.app` like any other application. It creates its writable Trash Bin, operation logs, and reports under:

```text
~/Library/Application Support/Clean My Codex
```

The build validates that every bundled Mach-O matches the package architecture and records the highest actual minimum macOS version. Closing the app requests an authenticated graceful shutdown; if a confirmed operation is active, macOS waits for it to finish or roll back before stopping the loopback service.

Existing Trash Bin data created by the source launcher remains in the checkout and is not copied or moved automatically. Use the source launcher to restore those older items. New native-app operations use Application Support.

Development builds are ad hoc signed. A stable downloadable public build must be Developer ID signed and notarized before release.

Unsigned open-source preview DMGs are architecture-specific and contain the same standalone app. After opening the DMG, drag **Clean My Codex.app** into **Applications**. Because the preview is not Apple notarized, the first launch may require Control-clicking the app and choosing **Open**. Later launches are ordinary double-click launches.

## Open the Windows App

Extract the Windows x64 package and open `Clean My Codex.exe`. Writable data is stored under:

```text
%LOCALAPPDATA%\Clean My Codex
```

The Windows shell uses WebView2 only for the embedded interface. It starts the same bundled loopback service, limits navigation to the active loopback port and approved project links, and waits for an authenticated safe-shutdown response before closing. If WebView2 is absent, install Microsoft's Evergreen Runtime and reopen the app.

Windows development packages are unsigned. A public executable should be Authenticode signed before release.

## Source Launcher

After cloning on macOS, either double-click `Clean My Codex.command` or run:

```bash
cd clean-my-codex
./run.command
```

The source launcher opens a protected session at `http://127.0.0.1:8765`. Keep its Terminal window open while using it. Open the interface through the launcher rather than a saved browser URL; each launch uses a new session capability.

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
- POSIX packages enforce owner-only modes; Windows rejects links and junctions and relies on inherited per-account profile ACLs rather than treating `chmod` as Windows access control.
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
python3 -m compileall -q clean_my_codex scripts script
node --check static/app.js
python3 scripts/build_release.py --output-dir release --replace
```

Build the standalone macOS app with an isolated build environment:

```bash
python3 -m venv build/pyinstaller-venv
build/pyinstaller-venv/bin/python -m pip install -r requirements-macos-build.txt
PYINSTALLER_PYTHON="$PWD/build/pyinstaller-venv/bin/python" ./script/build_macos_app.sh
```

The generated application is `dist/Clean My Codex.app`. Set `CLEAN_MY_CODEX_BUILD_ARCH=arm64` or `x86_64` to require a specific architecture. The build uses Apple Command Line Tools, bundles the Python service, generates the app icon, derives the real minimum macOS version, validates every Mach-O architecture, and applies an ad hoc development signature.

Build Windows x64 from PowerShell on Windows with .NET 8 SDK:

```powershell
python -m venv build\pyinstaller-venv
build\pyinstaller-venv\Scripts\python -m pip install -r requirements-windows-build.txt
./script/build_windows_app.ps1 -Python build\pyinstaller-venv\Scripts\python
```

The Windows build creates a self-contained WPF shell, bundled PyInstaller service, generated icon, license bundle, smoke-tested package, ZIP, and SHA-256 file. PyInstaller does not cross-compile operating systems, so macOS packages are built on their matching Mac runners and Windows packages on a Windows runner.

The `Desktop Packages` workflow builds separate Apple Silicon, Intel, and Windows x64 unsigned development artifacts on release branches. They are build evidence, not signed public releases.

For open-source testing, the same workflow also creates explicitly named `unsigned-preview.dmg` images for Apple Silicon and Intel. These can be attached only to a GitHub pre-release with the unsigned/not-notarized limitation kept visible; they are not a substitute for the protected signed-release workflow.

The manual `Signed Release` workflow builds an existing source tag, signs and notarizes both macOS architectures, Authenticode signs the Windows package, verifies all checksums, and then updates the matching GitHub release. It uses only credentials stored in the protected `release-signing` environment and defaults to keeping the release in pre-release state. See [SIGNING_AND_RELEASE.md](SIGNING_AND_RELEASE.md).

## Current Limits

- Codex storage formats are not a public compatibility contract. Unknown tables or references are left untouched and reported for review.
- On Windows, close Codex before applying a cleanup or relocation if Codex is holding an SQLite file lock. Clean My Codex fails and rolls back rather than forcing a locked replacement.
- Existing Trash Bin items are not migrated automatically between the source launcher, macOS Application Support, and Windows application data.
- Project cleanup removes Codex references and related chats; it never deletes the actual project directory.
- The automated Windows signing path supports exportable PFX certificates. Hardware-token and cloud-only certificates require a provider-specific signing integration.

The release builder copies only files listed in `PUBLIC_RELEASE_FILES.txt`. It rejects links and junctions, unsafe versions, runtime folders, database and session formats, machine paths, email addresses, UUID-like session identifiers, and common credential patterns before creating the archive.

For published releases, verify the ZIP checksum and confirm that the GitHub release points to the expected version tag. A checksum downloaded beside an archive detects transfer errors; the tag identifies the source commit the project published.

## Report an Issue

Use the bug button in the lower-right corner of the app or open the [Clean My Codex issue form](https://github.com/ethan3113/clean-my-codex/issues/new?template=bug_report.yml). Redact chat content, credentials, and identifying paths from screenshots or logs.

Security reports should follow [SECURITY.md](SECURITY.md).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests should stay narrowly scoped, preserve the preview-first safety model, and include tests for changes that can affect Codex data.

## License

Clean My Codex is released under the [MIT License](LICENSE). Bundled icon notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
