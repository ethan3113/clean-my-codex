#!/usr/bin/env python3
"""Detect the true minimum macOS version required by a built app bundle."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


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
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def parse_minos(output: str) -> list[str]:
    versions: list[str] = []
    command = ""
    for line in output.splitlines():
        value = line.strip()
        if value.startswith("Load command"):
            command = ""
        elif value == "cmd LC_BUILD_VERSION":
            command = "build"
        elif value == "cmd LC_VERSION_MIN_MACOSX":
            command = "legacy"
        elif command == "build" and value.startswith("minos "):
            version = value.removeprefix("minos ").strip()
            if VERSION_PATTERN.fullmatch(version):
                versions.append(version)
        elif command == "legacy" and value.startswith("version "):
            version = value.removeprefix("version ").strip()
            if VERSION_PATTERN.fullmatch(version):
                versions.append(version)
    return versions


def is_macho(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(4) in MACHO_MAGICS


def maximum_version(versions: list[str], floor: str) -> str:
    return max([floor, *versions], key=version_key)


def bundle_minimum(bundle: Path, floor: str) -> str:
    if shutil.which("vtool") is None:
        raise RuntimeError("vtool is required to validate macOS deployment targets")
    versions: list[str] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if not is_macho(path):
            continue
        result = subprocess.run(
            ["vtool", "-show-build", str(path)],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "vtool returned no diagnostic"
            raise RuntimeError(f"Unable to inspect Mach-O deployment target: {path}: {detail}")
        detected = parse_minos(result.stdout)
        if not detected:
            raise RuntimeError(f"Mach-O file has no readable macOS deployment target: {path}")
        versions.extend(detected)
    if not versions:
        raise RuntimeError("No Mach-O deployment targets were found in the app bundle")
    return maximum_version(versions, floor)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--floor", default="13.0")
    args = parser.parse_args()
    print(bundle_minimum(args.bundle.resolve(), args.floor))


if __name__ == "__main__":
    main()
