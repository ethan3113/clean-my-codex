#!/usr/bin/env python3
"""Package a macOS iconset into an ICNS container using only the standard library."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


RETINA_SUFFIX = chr(64) + "2x"


ICON_CHUNKS = (
    ("icp4", "icon_16x16.png"),
    ("ic11", f"icon_16x16{RETINA_SUFFIX}.png"),
    ("icp5", "icon_32x32.png"),
    ("ic12", f"icon_32x32{RETINA_SUFFIX}.png"),
    ("ic07", "icon_128x128.png"),
    ("ic13", f"icon_128x128{RETINA_SUFFIX}.png"),
    ("ic08", "icon_256x256.png"),
    ("ic14", f"icon_256x256{RETINA_SUFFIX}.png"),
    ("ic09", "icon_512x512.png"),
    ("ic10", f"icon_512x512{RETINA_SUFFIX}.png"),
)


def package_iconset(iconset: Path, output: Path) -> None:
    chunks: list[bytes] = []
    for chunk_type, filename in ICON_CHUNKS:
        source = iconset / filename
        payload = source.read_bytes()
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Expected a PNG icon: {source}")
        chunks.append(chunk_type.encode("ascii") + struct.pack(">I", len(payload) + 8) + payload)

    body = b"".join(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("iconset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package_iconset(args.iconset, args.output)


if __name__ == "__main__":
    main()
