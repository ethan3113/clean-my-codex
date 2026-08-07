# Signing and Release

Clean My Codex keeps development packaging separate from public distribution.

- `Desktop Packages` builds short-lived, unsigned development evidence.
- `Signed Release` is a manual, protected workflow for public downloads.
- A GitHub release is promoted only after all three signed packages and their checksums pass.

The signed workflow builds the exact source tag requested. Signing tools are loaded from the reviewed `main` commit that started the workflow, so an existing tag does not need to be rewritten when release automation improves.

## Required GitHub Environment

Create an Actions environment named `release-signing`. Restrict deployment branches to `main` and add an approval rule when the repository has another trusted maintainer.

Add these environment secrets:

| Secret | Purpose |
|---|---|
| `MACOS_CERTIFICATE_P12_BASE64` | Base64-encoded Developer ID Application certificate and private key export |
| `MACOS_CERTIFICATE_PASSWORD` | Passphrase protecting that P12 export |
| `APPLE_API_KEY_P8_BASE64` | Base64-encoded App Store Connect API private key |
| `APPLE_API_KEY_ID` | App Store Connect API key identifier |
| `APPLE_API_ISSUER_ID` | App Store Connect API issuer identifier |
| `WINDOWS_CERTIFICATE_PFX_BASE64` | Base64-encoded Windows code-signing certificate and private key export |
| `WINDOWS_CERTIFICATE_PASSWORD` | Passphrase protecting that PFX export |

Do not commit certificate files, private keys, passphrases, or encoded secret values. Do not paste them into issues, pull requests, workflow inputs, or support messages. Base64 is transport encoding, not encryption.

## macOS Identity

Public macOS distribution requires an active Apple Developer Program membership, a `Developer ID Application` certificate, and notarization credentials. Export the certificate and private key as a passphrase-protected P12. Create an App Store Connect API key that is permitted to submit software for notarization.

The release script:

1. Signs every nested Mach-O and code container before the outer app.
2. Enables hardened runtime and a secure timestamp.
3. Verifies Developer ID authority and Team Identifier.
4. Submits a temporary ZIP with `notarytool` and requires `Accepted` status.
5. Staples and validates the notarization ticket.
6. Runs strict code-signing and Gatekeeper assessments.
7. Creates the final ZIP and SHA-256 record.

No custom entitlements are added. If a future feature needs an entitlement, it must be reviewed separately instead of weakening the current release profile.

## Windows Identity

Public Windows distribution requires a trusted code-signing certificate. The current workflow supports a passphrase-protected, exportable PFX. Certificates held only in a hardware token or cloud signing service require a dedicated provider integration and should not be converted into an exportable key merely to fit this workflow.

The release script signs only project-owned files:

- `Clean My Codex.exe`
- `Clean My Codex.dll`
- `server/clean-my-codex-server.exe`

Third-party .NET, WebView2, and Python runtime files are not re-signed. SignTool uses SHA-256, an RFC 3161 timestamp, Windows policy verification, and Authenticode verification before the package is rebuilt.

## Run a Signed Release

1. Create the source release and tag, such as `v0.3.0`.
2. Open **Actions**, select **Signed Release**, and run it from `main`.
3. Enter the existing tag.
4. Leave **promote release** disabled for the first signed build.
5. Download and open each pre-release package on a clean matching system.
6. Run the workflow with promotion enabled only when all public-install checks are complete, or promote the verified release through GitHub's release controls.

The workflow refuses non-`main` dispatches, malformed or mismatched tags, missing GitHub releases, absent credentials, failed notarization, invalid Authenticode signatures, incomplete asset sets, and checksum mismatches. Signed assets are uploaded only after Apple Silicon, Intel, and Windows jobs all succeed.

## Local Script Entry Points

The scripts can also be used in a controlled release environment after building the matching package:

```bash
CLEAN_MY_CODEX_SIGN_IDENTITY="Developer ID Application: ..." \
APPLE_API_KEY_PATH="/secure/path/AuthKey.p8" \
APPLE_API_KEY_ID="..." \
APPLE_API_ISSUER_ID="..." \
./script/sign_macos_release.sh \
  "dist/Clean My Codex.app" \
  "dist/Clean-My-Codex-macOS-arm64-v0.3.0.zip"
```

```powershell
./script/sign_windows_release.ps1 `
  -PackageRoot "dist\Clean My Codex Windows x64" `
  -CertificateThumbprint "CERTIFICATE_THUMBPRINT" `
  -OutputArchive "dist\Clean-My-Codex-Windows-x64-v0.3.0.zip"
```

The scripts intentionally stop when a required identity, private key, timestamp, notarization result, or final verification is unavailable.
