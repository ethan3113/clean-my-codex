#!/usr/bin/env python3
"""Locate the vendored license for the CPython runtime used by a build."""

from __future__ import annotations

import sys
from pathlib import Path


def runtime_license(root: Path, version_info: tuple[int, int] | None = None) -> Path:
    major, minor = version_info or sys.version_info[:2]
    path = root.resolve() / "licenses" / f"CPython-{major}.{minor}-LICENSE.txt"
    if not path.is_file():
        raise RuntimeError(f"No vendored CPython license is available for {major}.{minor}")
    text = path.read_text(encoding="utf-8")
    required = (
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
        "BEOPEN.COM LICENSE AGREEMENT FOR PYTHON 2.0",
    )
    if any(marker not in text for marker in required):
        raise RuntimeError(f"Vendored CPython license is incomplete: {path}")
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: cpython_runtime_license.py PROJECT_ROOT")
    print(runtime_license(Path(sys.argv[1])))
