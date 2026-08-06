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

## Package

- [ ] Build with `python3 scripts/build_release.py --output-dir release --replace`.
- [ ] Inspect `release/clean-my-codex-v<version>/`.
- [ ] Confirm the ZIP hash matches the generated SHA-256 file.
- [ ] Confirm the archive contains no runtime folders, reports, databases, JSONL files, credentials, or installation-specific paths.
- [ ] Run `python3 scripts/audit_release.py release/clean-my-codex-v<version>` against the staged package.
- [ ] Confirm the repository inventory contains only paths from `PUBLIC_RELEASE_FILES.txt` before the first commit.
- [ ] Confirm every GitHub Action is pinned to a full commit SHA.

## GitHub

- [ ] Create the public repository as `clean-my-codex`.
- [ ] Enable private vulnerability reporting before the first public push.
- [ ] Review every staged path explicitly.
- [ ] Commit the audited source only.
- [ ] Push after reviewing the commit.
- [ ] Create and verify the release tag `v<version>`.
- [ ] Attach the audited ZIP plus SHA-256 file to the GitHub release created from that tag.
