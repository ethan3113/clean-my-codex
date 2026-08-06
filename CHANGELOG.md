# Changelog

## 0.2.0 - 2026-08-06

- Added a standalone macOS application with a native AppKit window, embedded WebKit interface, original app icon, and bundled Python service.
- Added one-click LaunchServices startup with no Terminal window and automatic service shutdown when the app quits.
- Separated read-only interface resources from writable application data under Application Support.
- Added private ephemeral-port handoff, owner-only session files, restricted in-app navigation, and non-persistent WebKit storage.
- Added reproducible macOS packaging scripts, pinned PyInstaller build requirements, ICNS packaging, ad hoc development signing, and native-app tests.
- Added bundle-wide Mach-O deployment-target detection so the final app reports its actual minimum macOS requirement.
- Made the native ready-file handoff atomic and kept existing source-launcher Trash Bin data untouched.
- Added an authenticated graceful-shutdown handshake so Quit cannot interrupt an active cleanup or relocation operation.

## 0.1.0 - 2026-08-05

- Established the public product name, package, launcher, repository links, original logo, and issue form for Clean My Codex.
- Introduced one cleanup model: preview, typed confirmation, Trash Bin, restore, or permanent deletion from Trash Bin only.
- Added Chat Manager, Missing Paths, Relocation Manager, Folder Cleaner, Trash Bin, and Operation Logs views.
- Rebuilt the interface with a compact neutral developer-tool design, contextual detail panels, circular animated checkboxes, semantic status icons, skeleton scanning states, and one-second navigation motion.
- Added exact chat and project reference discovery across session files, SQLite state, session index records, global state, and project configuration.
- Added one-time preview receipts bound to the exact action, selected targets, and current metadata state.
- Added transactional SQLite deletion and row-level restore with integrity and foreign-key checks.
- Added compare-before-write protection for JSONL, JSON, TOML, and SQLite metadata, including active write-ahead-log state.
- Added operation-owned rollback fingerprints so concurrent Codex changes are preserved instead of replaced by older snapshots.
- Restricted the server to numeric loopback, protected every data API with a per-launch capability, and kept that capability out of process arguments and terminal output.
- Disabled legacy whole-file restore and legacy cleanup mutation routes when modern safety evidence is unavailable.
- Added an allowlisted deterministic release builder, complete publication-inventory audit, common credential detection, pinned CI actions, and public release checks.
- Added the MIT license, security policy, contribution guide, safety policy, Trash Bin documentation, product specification, and third-party notices.
