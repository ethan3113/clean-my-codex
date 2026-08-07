# Release Checklist

## Source

- [ ] `APP_VERSION` matches the intended tag.
- [ ] Product name is `Clean My Codex` everywhere.
- [ ] Repository and issue links target `ethan3113/clean-my-codex`.
- [ ] Navigation motion completes in one second and honors reduced motion.
- [ ] No runtime output or generated report is staged.

## Verification

- [ ] Unit tests pass.
- [ ] Python modules compile.
- [ ] JavaScript syntax check passes.
- [ ] Desktop and narrow viewport browser checks pass.
- [ ] Chat deletion preview works without changing active data.
- [ ] Release audit reports zero findings.
- [ ] Every security candidate has a final disposition, with no unresolved reportable or deferred high-impact finding.
- [ ] Bare `/api/health` exposes no capability or filesystem path and data APIs reject missing tokens.
- [ ] Restore stops when protected metadata changed after deletion.
- [ ] Linux/Python compatibility and Windows x64 tests pass in CI.

## macOS App

- [ ] Build `Clean My Codex.app` with the pinned packaging requirement.
- [ ] Verify the app opens through LaunchServices without a Terminal window.
- [ ] Verify Quit stops the bundled loopback service and removes temporary session files.
- [ ] Verify Quit waits for a blocked synthetic mutation to complete before the service exits.
- [ ] Confirm the app contains CPython and PyInstaller license texts.
- [ ] Confirm `LSMinimumSystemVersion` equals the highest deployment target among all bundled Mach-O files.
- [ ] Confirm every Mach-O contains only the package architecture (`arm64` or `x86_64`).
- [ ] Audit every bundled executable, dynamic library, resource, and documentation file.
- [ ] Sign with a Developer ID Application identity and enable hardened runtime.
- [ ] Notarize the final archive and staple the ticket before publishing it.
- [ ] Verify Gatekeeper acceptance on a clean macOS account.

## Windows App

- [ ] Build the WPF shell and PyInstaller service on a Windows x64 runner.
- [ ] Verify the packaged `Clean My Codex.exe --smoke-test` starts, passes health, and shuts down safely.
- [ ] Verify the app opens without a console window and uses the expected `%LOCALAPPDATA%` data directory.
- [ ] Verify the embedded browser blocks non-loopback navigation and unapproved external destinations.
- [ ] Confirm CPython, PyInstaller, and WebView2 license and notice files are included.
- [ ] Verify operation rollback behavior while Codex has the active SQLite database open.
- [ ] Authenticode sign the executable and verify the signature on a clean Windows account.

## Package

- [ ] Build with `python3 scripts/build_release.py --output-dir release --replace`.
- [ ] Inspect `release/clean-my-codex-v<version>/`.
- [ ] Confirm the ZIP hash matches the generated SHA-256 file.
- [ ] Confirm the archive contains no runtime folders, reports, databases, JSONL files, credentials, or installation-specific paths.
- [ ] Run `python3 scripts/audit_release.py release/clean-my-codex-v<version>` against the staged package.
- [ ] Confirm the repository inventory contains only paths from `PUBLIC_RELEASE_FILES.txt` before the first commit.
- [ ] Confirm every GitHub Action is pinned to a full commit SHA.
- [ ] Confirm workflow artifacts are named as unsigned development builds and expire automatically.
- [ ] Confirm the signed release asset verifier accepts exactly three ZIPs and three matching SHA-256 files.

## GitHub

- [ ] Create the public repository as `clean-my-codex`.
- [ ] Enable private vulnerability reporting before the first public push.
- [ ] Review every staged path explicitly.
- [ ] Commit the audited source only.
- [ ] Push after reviewing the commit.
- [ ] Create and verify the release tag `v<version>`.
- [ ] Attach the audited ZIP plus SHA-256 file to the GitHub release created from that tag.
- [ ] Attach signed and notarized macOS archives only after each architecture-specific audit passes.
- [ ] Attach the Authenticode-signed Windows x64 archive only after the Windows audit passes.
- [ ] Do not attach unsigned CI development artifacts to a public release.
- [ ] Run `Signed Release` from `main` through the protected `release-signing` environment.
- [ ] Keep promotion disabled until all signed packages open successfully on clean matching systems.
