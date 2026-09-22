"""Generate the PWA icons as real PNGs, with no image library required.

A minimal PNG encoder is all this needs: the icon is flat colour blocks - a
dark rounded tile with an ascending bar chart and a downward tick, which reads
clearly at 64px on a phone home screen.

Run:  python scripts/make_icons.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"

BG = (15, 21, 33)        # near-black navy
TILE = (23, 32, 48)      # slightly lifted tile
UP = (232, 183, 74)      # gold - buys
DOWN = (214, 91, 84)     # red - sells
TEXT = (233, 238, 247)


def _rounded(x: int, y: int, size: int, radius: int) -> bool:
    """Inside a rounded square of `size` with corner `radius`?"""
    if x < 0 or y < 0 or x >= size or y >= size:
        return False
    for cx, cy in ((radius, radius), (size - radius, radius),
                   (radius, size - radius), (size - radius, size - radius)):
        inside_x = x < radius if cx == radius else x > size - radius
        inside_y = y < radius if cy == radius else y > size - radius
        if inside_x and inside_y:
            return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2
    return True


def render(size: int) -> list[list[tuple[int, int, int]]]:
    px = [[BG for _ in range(size)] for _ in range(size)]
    radius = max(2, size * 22 // 100)

    for y in range(size):
        for x in range(size):
            if _rounded(x, y, size, radius):
                px[y][x] = TILE

    # Ascending bars, then one falling bar: trades in, trades out.
    margin = size * 18 // 100
    floor = size - margin
    bar_w = max(2, size * 11 // 100)
    gap = max(1, size * 5 // 100)
    heights = [0.30, 0.50, 0.72, 0.42]
    colours = [UP, UP, UP, DOWN]

    x = margin
    for height, colour in zip(heights, colours):
        top = floor - int((floor - margin) * height)
        for yy in range(top, floor):
            for xx in range(x, min(x + bar_w, size - margin)):
                if _rounded(xx, yy, size, radius):
                    px[yy][xx] = colour
        x += bar_w + gap

    # Baseline rule.
    for xx in range(margin, size - margin):
        for yy in range(floor, floor + max(1, size // 64)):
            if _rounded(xx, yy, size, radius):
                px[yy][xx] = TEXT
    return px


def write_png(path: Path, pixels: list[list[tuple[int, int, int]]]) -> None:
    height, width = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        path = OUT / f"icon-{size}.png"
        write_png(path, render(size))
        print(f"  wrote {path.relative_to(OUT.parent.parent)}  ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
