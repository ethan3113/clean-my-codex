#!/usr/bin/env python3
"""Fail closed when a macOS app contains the wrong Mach-O architecture."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

SUPPORTED_ARCHITECTURES = {"arm64", "x86_64"}
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def is_macho(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(4) in MACHO_MAGICS


def parse_architectures(output: str) -> set[str]:
    value = output.strip()
    if " are: " in value:
        value = value.rsplit(" are: ", 1)[1]
    elif " architecture: " in value:
        value = value.rsplit(" architecture: ", 1)[1]
    return {part for part in value.split() if part in SUPPORTED_ARCHITECTURES}


def validate_bundle_architecture(bundle: Path, expected: str) -> int:
    if expected not in SUPPORTED_ARCHITECTURES:
        raise RuntimeError(f"Unsupported macOS architecture: {expected}")
    if shutil.which("lipo") is None:
        raise RuntimeError("lipo is required to validate macOS architectures")
    inspected = 0
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.is_symlink() or not is_macho(path):
            continue
        result = subprocess.run(
            ["lipo", "-archs", str(path)],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "lipo returned no diagnostic"
            raise RuntimeError(f"Unable to inspect Mach-O architecture: {path}: {detail}")
        architectures = parse_architectures(result.stdout)
        if architectures != {expected}:
            found = ", ".join(sorted(architectures)) or "unknown"
            raise RuntimeError(f"Mach-O architecture mismatch: {path}: expected {expected}, found {found}")
        inspected += 1
    if inspected == 0:
        raise RuntimeError("No Mach-O files were found in the app bundle")
    return inspected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()
    count = validate_bundle_architecture(args.bundle.resolve(), args.expected)
    print(count)


if __name__ == "__main__":
    main()
