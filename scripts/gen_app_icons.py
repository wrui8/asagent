"""生成 PWA 与 Tauri 图标（纯标准库，无第三方依赖）。

用法：uv run python scripts/gen_app_icons.py
输出：
- web/public/icons/icon-192.png、icon-512.png
- desktop/src-tauri/icons/{32x32,128x128,128x128@2x,icon}.png、icon.ico
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "web" / "public" / "icons"
TAURI_DIR = ROOT / "desktop" / "src-tauri" / "icons"


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _gradient(t: float) -> tuple[int, int, int]:
    stops = [
        (0.0, (0, 229, 255)),
        (0.42, (99, 102, 241)),
        (0.72, (168, 85, 247)),
        (1.0, (244, 114, 182)),
    ]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:], strict=False):
        if t <= p1:
            span = max(p1 - p0, 1e-6)
            return _lerp(c0, c1, (t - p0) / span)
    return stops[-1][1]


def _rounded_alpha(x: int, y: int, size: int, radius: float) -> int:
    """圆角矩形的抗锯齿 alpha。"""
    cx = min(max(x + 0.5, radius), size - radius)
    cy = min(max(y + 0.5, radius), size - radius)
    dist = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
    return max(0, min(255, round((radius - dist + 0.5) * 255)))


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    u = x / max(size - 1, 1)
    v = y / max(size - 1, 1)
    r, g, b = _gradient(u * 0.65 + v * 0.35)

    # 中心发光圆环：科技感几何，不依赖字体
    dx = u - 0.5
    dy = v - 0.5
    dist = math.hypot(dx, dy)
    ring = abs(dist - 0.205)
    if ring < 0.012:
        glow = 1 - ring / 0.012
        r = round(r + (255 - r) * glow * 0.9)
        g = round(g + (255 - g) * glow * 0.9)
        b = round(b + (255 - b) * glow * 0.9)
    dot = math.hypot(dx, dy + 0.0)
    if dot < 0.028:
        r, g, b = 255, 255, 255
    # 外圈细轨道
    orbit = abs(dist - 0.34)
    if orbit < 0.004:
        r = round(r * 0.55 + 255 * 0.45)
        g = round(g * 0.55 + 255 * 0.45)
        b = round(b * 0.55 + 255 * 0.45)

    alpha = _rounded_alpha(x, y, size, size * 0.22)
    return r, g, b, alpha


def _png_bytes(size: int) -> bytes:
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter type 0
        for x in range(size):
            rows.extend(_pixel(x, y, size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"wrote {path} ({len(data)} bytes)")


def _ico_bytes(sizes: tuple[int, ...]) -> bytes:
    """多尺寸 ICO；使用 PNG 压缩帧（Vista+ 支持，Tauri/NSIS 可用）。"""
    frames = {size: _png_bytes(size) for size in sizes}
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    blobs = bytearray()
    for size, png in sorted(frames.items()):
        side = 0 if size >= 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset
            )
        )
        blobs.extend(png)
        offset += len(png)
    return header + bytes(entries) + bytes(blobs)


def main() -> None:
    for size in (192, 512):
        _write(OUT_DIR / f"icon-{size}.png", _png_bytes(size))

    _write(TAURI_DIR / "32x32.png", _png_bytes(32))
    _write(TAURI_DIR / "128x128.png", _png_bytes(128))
    _write(TAURI_DIR / "128x128@2x.png", _png_bytes(256))
    _write(TAURI_DIR / "icon.png", _png_bytes(512))
    _write(TAURI_DIR / "icon.ico", _ico_bytes((16, 32, 48, 64, 128, 256)))


if __name__ == "__main__":
    main()
