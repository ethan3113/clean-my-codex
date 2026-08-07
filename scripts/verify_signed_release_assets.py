from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CHECKSUM_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")
PACKAGE_TEMPLATES = (
    "Clean-My-Codex-macOS-arm64-v{version}.zip",
    "Clean-My-Codex-macOS-x86_64-v{version}.zip",
    "Clean-My-Codex-Windows-x64-v{version}.zip",
)


def expected_asset_names(version: str) -> set[str]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Unsafe release version: {version!r}")
    packages = {template.format(version=version) for template in PACKAGE_TEMPLATES}
    return packages | {f"{name}.sha256" for name in packages}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_assets(directory: Path, version: str) -> dict[str, str]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise RuntimeError(f"Release asset directory does not exist: {directory}")

    expected = expected_asset_names(version)
    entries = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise RuntimeError("Release assets must be regular files in one directory")

    actual = {path.name for path in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise RuntimeError(
            f"Signed release asset inventory mismatch; missing={missing}, unexpected={unexpected}"
        )

    verified: dict[str, str] = {}
    for package_name in sorted(name for name in expected if name.endswith(".zip")):
        package = directory / package_name
        checksum_file = directory / f"{package_name}.sha256"
        checksum_text = checksum_file.read_text(encoding="ascii").strip()
        match = CHECKSUM_PATTERN.fullmatch(checksum_text)
        if not match or match.group(2) != package_name:
            raise RuntimeError(f"Invalid checksum record: {checksum_file.name}")
        actual_hash = sha256(package)
        if match.group(1) != actual_hash:
            raise RuntimeError(f"Checksum mismatch: {package_name}")
        verified[package_name] = actual_hash
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify signed Clean My Codex release assets")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    verified = verify_assets(args.directory, args.version)
    print(f"Verified {len(verified)} signed package(s) and checksums.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
