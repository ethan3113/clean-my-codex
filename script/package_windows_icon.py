#!/usr/bin/env python3
"""Generate the Clean My Codex Windows icon without binary source assets."""

from __future__ import annotations

import argparse
import binascii
import struct
import zlib
from pathlib import Path


SIZE = 256
SCALE = 4


def _inside_rounded_rect(x: float, y: float, box: tuple[float, float, float, float], radius: float) -> bool:
    left, top, right, bottom = box
    if not (left <= x < right and top <= y < bottom):
        return False
    nearest_x = min(max(x, left + radius), right - radius)
    nearest_y = min(max(y, top + radius), bottom - radius)
    dx = x - nearest_x
    dy = y - nearest_y
    return dx * dx + dy * dy <= radius * radius


def _distance_to_segment(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    value = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + value * dx, ay + value * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _blend(base: tuple[int, int, int, int], top: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    alpha = top[3] / 255
    inverse = 1 - alpha
    return (
        round(top[0] * alpha + base[0] * inverse),
        round(top[1] * alpha + base[1] * inverse),
        round(top[2] * alpha + base[2] * inverse),
        round((alpha + base[3] / 255 * inverse) * 255),
    )


def _sample(x: float, y: float) -> tuple[int, int, int, int]:
    pixel = (0, 0, 0, 0)
    outer = (6, 6, 250, 250)
    inner = (12, 12, 244, 244)
    if _inside_rounded_rect(x, y, outer, 50):
        pixel = (58, 58, 58, 255)
    if _inside_rounded_rect(x, y, inner, 44):
        pixel = (10, 10, 10, 255)

    blocks = [
        ((60, 64, 112, 116), (237, 237, 237, 255)),
        ((140, 64, 196, 116), (237, 237, 237, 107)),
        ((60, 144, 112, 192), (237, 237, 237, 107)),
    ]
    for box, color in blocks:
        if _inside_rounded_rect(x, y, box, 10):
            pixel = _blend(pixel, color)

    points = [(192, 150), (192, 170), (188, 182), (176, 192), (140, 192)]
    points += [(158, 174), (140, 192), (158, 210)]
    if any(_distance_to_segment(x, y, a, b) <= 6.8 for a, b in zip(points, points[1:])):
        pixel = _blend(pixel, (237, 237, 237, 255))
    return pixel


def render_rgba() -> bytes:
    output = bytearray()
    for y in range(SIZE):
        for x in range(SIZE):
            samples = [
                _sample(x + (sx + 0.5) / SCALE, y + (sy + 0.5) / SCALE)
                for sy in range(SCALE)
                for sx in range(SCALE)
            ]
            output.extend(round(sum(value[channel] for value in samples) / len(samples)) for channel in range(4))
    return bytes(output)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)


def png_payload(rgba: bytes) -> bytes:
    rows = b"".join(b"\0" + rgba[y * SIZE * 4 : (y + 1) * SIZE * 4] for y in range(SIZE))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b"")


def icon_payload(png: bytes) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(icon_payload(png_payload(render_rgba())))


if __name__ == "__main__":
    main()
