from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


MAX_PUBLIC_FILE_SIZE = 2 * 1024 * 1024
FORBIDDEN_PARTS = {
    ".git",
    ".impeccable",
    "__pycache__",
    "backups",
    "build",
    "codex-archive",
    "dist",
    "operation-logs",
    "purge-backups",
    "release",
    "trash",
    "trash-bin",
}
FORBIDDEN_NAMES = {
    ".DS_Store",
    ".codex-global-state.json",
    "auth.json",
    "CODEX_DATA_STRUCTURE_REPORT.md",
    "CODEX_FOLDER_CLEANLINESS_REPORT.md",
    "config.toml",
    "session_index.jsonl",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".jsonl",
    ".key",
    ".pem",
    ".pyc",
    ".sqlite",
}
GENERIC_SYSTEM_ACCOUNTS = {
    "admin",
    "administrator",
    "codespace",
    "root",
    "runner",
    "ubuntu",
    "user",
}


def is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def _is_identifying_account_name(account_name: str) -> bool:
    normalized = account_name.strip().lower()
    return len(normalized) >= 4 and normalized not in GENERIC_SYSTEM_ACCOUNTS


def _content_patterns() -> list[tuple[str, re.Pattern[str]]]:
    mac_home = "/" + "Users" + "/"
    linux_home = "/" + "home" + "/"
    windows_home = "C:" + "\\" + "Users" + "\\"
    fine_grained_github_prefix = "github" + "_pat_"
    stripe_live_prefix = "sk_" + "live_"
    slack_bot_prefix = "xox" + "b-"
    npm_token_prefix = "npm" + "_"
    credential_names = "|".join(
        [
            "api[_-]?key",
            "access[_-]?token",
            "secret[_-]?key",
            "client[_-]?secret",
            "oauth[_-]?secret",
            "session[_-]?token",
            "private[_-]?token",
            "database[_-]?url",
            "cookie",
            "password",
        ]
    )
    return [
        ("macOS home path", re.compile(re.escape(mac_home) + r"[A-Za-z0-9._-]+(?=$|[/\\\s'\"`])")),
        ("Linux home path", re.compile(re.escape(linux_home) + r"[A-Za-z0-9._-]+(?=$|[/\\\s'\"`])")),
        ("Windows home path", re.compile(re.escape(windows_home), re.IGNORECASE)),
        ("email address", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
        (
            "UUID-like session identifier",
            re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        ),
        ("private key", re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----")),
        ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
        (
            "GitHub fine-grained token",
            re.compile(r"\b" + re.escape(fine_grained_github_prefix) + r"[A-Za-z0-9_]{20,}\b"),
        ),
        ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
        ("OpenAI-style token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        ("Stripe live secret", re.compile(r"\b" + re.escape(stripe_live_prefix) + r"[A-Za-z0-9]{16,}\b")),
        ("Slack bot token", re.compile(r"\b" + re.escape(slack_bot_prefix) + r"[A-Za-z0-9-]{20,}\b")),
        ("npm access token", re.compile(r"\b" + re.escape(npm_token_prefix) + r"[A-Za-z0-9]{20,}\b")),
        ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
        ("bearer token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}\b")),
        (
            "credential assignment",
            re.compile(
                r"(?i)\b(?:" + credential_names + r")"
                r"\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"
            ),
        ),
        (
            "unquoted credential assignment",
            re.compile(
                r"(?im)^\s*(?:" + credential_names + r")"
                r"\s*[:=]\s*(?!['\"])[^\s#]{8,}\s*$"
            ),
        ),
        (
            "credential-bearing URL",
            re.compile(
                r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
                r"[^\s:/@]+:[^\s/@]+@[^\s]+"
            ),
        ),
        (
            "generic credential assignment",
            re.compile(
                r"(?m)^\s*(?:SESSION_TOKEN|TOKEN|COOKIE|DATABASE_URL|PRIVATE_TOKEN)"
                r"\s*[:=]\s*['\"](?!\$\(|\$\{)[^'\"\n]{8,}['\"]\s*$"
            ),
        ),
    ]


def audit_tree(root: Path, ignore_git_metadata: bool = False) -> list[Finding]:
    root = root.resolve()
    if not root.is_dir():
        return [Finding(str(root), "release root does not exist")]

    findings: list[Finding] = []
    home = str(Path.home().resolve())
    account_name = Path.home().name.lower()
    patterns = _content_patterns()

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ignore_git_metadata and ".git" in relative.parts:
            continue
        relative_text = relative.as_posix()
        lower_parts = {part.lower() for part in relative.parts}

        if is_link_like(path):
            findings.append(Finding(relative_text, "links and junctions are not allowed"))
            continue
        blocked_parts = sorted(lower_parts.intersection(FORBIDDEN_PARTS))
        if blocked_parts:
            findings.append(Finding(relative_text, f"forbidden path component: {blocked_parts[0]}"))
            continue
        if path.is_dir():
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            findings.append(Finding(relative_text, "forbidden environment filename"))
            continue
        if path.name in FORBIDDEN_NAMES:
            findings.append(Finding(relative_text, "forbidden runtime filename"))
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(Finding(relative_text, f"forbidden runtime format: {path.suffix.lower()}"))
            continue
        if path.stat().st_size > MAX_PUBLIC_FILE_SIZE:
            findings.append(Finding(relative_text, "file exceeds the public size limit"))
            continue

        payload = path.read_bytes()
        if b"\x00" in payload:
            findings.append(Finding(relative_text, "binary content is not allowlisted"))
            continue
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(relative_text, "non-UTF-8 content is not allowlisted"))
            continue

        lowered = content.lower()
        if home and home in content:
            findings.append(Finding(relative_text, "current machine home path"))
        if _is_identifying_account_name(account_name) and account_name in lowered:
            findings.append(Finding(relative_text, "current machine account name"))
        for reason, pattern in patterns:
            if pattern.search(content):
                findings.append(Finding(relative_text, reason))

    return sorted(set(findings), key=lambda finding: (finding.path, finding.reason))


def audit_publication_tree(root: Path, allowlist_path: Path) -> list[Finding]:
    root = root.resolve()
    allowlist_path = allowlist_path.resolve()
    expected: set[str] = set()
    for raw_line in allowlist_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\\" in line:
            return [Finding(str(allowlist_path), f"unsafe allowlist entry: {line!r}")]
        relative = Path(line)
        if relative.is_absolute() or ".." in relative.parts:
            return [Finding(str(allowlist_path), f"unsafe allowlist entry: {line!r}")]
        expected.add(relative.as_posix())

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts and (path.is_file() or is_link_like(path))
    }
    findings = audit_tree(root, ignore_git_metadata=True)
    findings.extend(Finding(path, "file is not listed for public release") for path in sorted(actual - expected))
    findings.extend(Finding(path, "allowlisted public file is missing") for path in sorted(expected - actual))
    return sorted(set(findings), key=lambda finding: (finding.path, finding.reason))


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "Release audit passed: no blocked data or formats found."
    lines = [f"Release audit failed with {len(findings)} finding(s):"]
    lines.extend(f"- {finding.path}: {finding.reason}" for finding in findings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a Clean My Codex public release tree")
    parser.add_argument("path", type=Path)
    parser.add_argument("--allowlist", type=Path)
    args = parser.parse_args(argv)
    findings = (
        audit_publication_tree(args.path, args.allowlist)
        if args.allowlist
        else audit_tree(args.path)
    )
    print(format_findings(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
