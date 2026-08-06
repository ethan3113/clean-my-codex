# Security Policy

## Supported Versions

Security fixes are applied to the latest published release.

| Version | Supported |
|---|---|
| 0.2.x | Development |
| 0.1.x | Yes |
| Earlier | No |

## Reporting a Vulnerability

Do not open a public issue for a vulnerability that could expose Codex data, bypass confirmation, alter unrelated records, or permit non-loopback write access.

Use [GitHub's private vulnerability report](https://github.com/ethan3113/clean-my-codex/security/advisories/new) and include:

- The affected version and macOS version.
- The smallest reproducible sequence.
- The expected and observed safety boundary.
- A redacted proof of concept.
- Whether any active Codex data changed.

Do not attach credentials, `auth.json`, session JSONL content, SQLite databases, operation logs, or identifying workspace paths.

## Security Boundaries

- The web server binds only to numeric loopback address `127.0.0.1`.
- Requests must use a loopback Host header.
- Every data API requires a per-launch random capability; write requests additionally require JSON and a same-origin browser context when Origin is present.
- The unauthenticated health response exposes no capability, filesystem path, or data-directory status.
- The source launcher and native app store the capability in owner-readable mode-`600` files. The capability enters the interface in a URL fragment and is then cleared from navigation history.
- The native app uses a non-persistent WebKit data store, accepts in-window navigation only to its own ephemeral loopback port, and opens approved external links in the default browser.
- Native-app Quit is deferred behind the server's operation lock; no new mutation is accepted after the authenticated shutdown handshake begins.
- Every apply request consumes a short-lived, one-time preview receipt bound to the exact action, targets, and current preview.
- Active Codex records are never permanently deleted directly.
- The public release builder uses an explicit allowlist and rejects runtime data formats and common secrets.
- Trash restore uses exact SQLite row exports and guarded text snapshots, and blocks restoration when protected metadata changed after deletion.
- Published archives should be tied to a verified signed repository tag; a side-by-side checksum alone is not an authenticity guarantee.
- A downloadable macOS app should be Developer ID signed and notarized before publication. Ad hoc signatures are for development builds only.

The app operates on evolving Codex internal formats. A schema it cannot prove safe is skipped and reported for review.
