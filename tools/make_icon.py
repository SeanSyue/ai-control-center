r"""Build packaging/aicc.ico from packaging/icon-source.png.

Run from the repo root:
    .\.venv\Scripts\python.exe tools\make_icon.py

The artwork is a rounded-square tile drawn against an opaque black backdrop
with a margin around it. Two things have to happen before it is an icon rather
than a picture, and neither changes the design:

* **Crop to the tile.** The margin is dead space. Kept, it shrinks the artwork
  inside the icon cell and the mark loses roughly a quarter of its size at
  every size Windows draws.

* **Cut the corners out.** The backdrop is opaque black, not transparent, so
  the rounded corners are filled in - the tile renders as a plain square, and
  on a light taskbar as a *black* square. Making the outside transparent is
  what lets the rounded shape the artist drew actually appear.

The corner cut is a flood fill from the four corners rather than a
rounded-rectangle mask with a guessed radius: it follows the artwork's own edge
exactly, so there is nothing to get wrong. It stops at the tile's border stroke,
which is far brighter than the threshold.

Qt has no multi-size ICO writer, so the frames are assembled by hand below.
PNG-compressed frames are valid in .ico from Vista onward.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import deque
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "icon-source.png"
OUT = ROOT / "packaging" / "aicc.ico"

# Windows picks the nearest available size. Without the small ones it downscales
# 256px itself and the mark turns to mush in the taskbar.
SIZES = (16, 24, 32, 48, 64, 128, 256)

# Anything this dark is backdrop, not artwork. The tile's own darkest interior
# measures ~35 (r+g+b) and its border stroke ~190, so there is a wide margin -
# but the value also has to be high enough to catch the antialiased pixels where
# the rounded edge fades into black, or they survive as a dark fringe.
BLACK_THRESHOLD = 20


def _tile_bounds(image: QImage) -> tuple[int, int, int, int]:
    """The artwork's bounding box, ignoring the black margin around it."""
    width, height = image.width(), image.height()

    def row_has_art(y: int) -> bool:
        return any(
            sum(image.pixelColor(x, y).getRgb()[:3]) > BLACK_THRESHOLD
            for x in range(0, width, 2)
        )

    def column_has_art(x: int) -> bool:
        return any(
            sum(image.pixelColor(x, y).getRgb()[:3]) > BLACK_THRESHOLD
            for y in range(0, height, 2)
        )

    top = next(y for y in range(height) if row_has_art(y))
    bottom = next(y for y in range(height - 1, -1, -1) if row_has_art(y))
    left = next(x for x in range(width) if column_has_art(x))
    right = next(x for x in range(width - 1, -1, -1) if column_has_art(x))
    return left, top, right - left + 1, bottom - top + 1


def _clear_outside(image: QImage) -> QImage:
    """Make the black outside the rounded tile transparent.

    Flood fill from the corners, so only backdrop connected to the edge is
    cleared. A dark pixel enclosed by the artwork - inside the tile, behind the
    orbits - is never reached, which a plain threshold would have punched a hole
    through.
    """
    result = image.convertToFormat(QImage.Format_ARGB32)
    width, height = result.width(), result.height()
    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    for start in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        queue.append(start)

    transparent = 0x00000000
    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height):
            continue
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1
        red, green, blue, _ = result.pixelColor(x, y).getRgb()
        if red + green + blue > BLACK_THRESHOLD:
            continue          # artwork edge: stop, and keep the pixel
        result.setPixel(x, y, transparent)
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return result


def _write_ico(path: Path, images: list[QImage]) -> None:
    frames: list[tuple[int, bytes]] = []
    for image in images:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        frames.append((image.width(), bytes(data)))

    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(frames)))       # ICONDIR
    offset = 6 + 16 * len(frames)
    for size, payload in frames:
        # 256 is stored as 0 - the directory entry's field is one byte.
        dimension = 0 if size >= 256 else size
        out.write(
            struct.pack(
                "<BBBBHHII",
                dimension, dimension, 0, 0, 1, 32, len(payload), offset,
            )
        )
        offset += len(payload)
    for _, payload in frames:
        out.write(payload)

    path.write_bytes(out.getvalue())


def main() -> int:
    if not SOURCE.is_file():
        print(f"找不到來源圖：{SOURCE}")
        return 1

    QApplication(sys.argv)
    source = QImage(str(SOURCE))
    if source.isNull():
        print(f"讀不到來源圖：{SOURCE}")
        return 1

    left, top, width, height = _tile_bounds(source)
    # Square it off, so no size ends up stretched.
    side = max(width, height)
    left -= (side - width) // 2
    top -= (side - height) // 2
    cropped = source.copy(left, top, side, side)
    print(f"來源 {source.width()}x{source.height()} → 裁到磚塊 {side}x{side}")

    masked = _clear_outside(cropped)

    frames = [
        masked.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        for size in SIZES
    ]
    _write_ico(OUT, frames)
    print(f"寫出 {OUT}  ({OUT.stat().st_size} bytes, {len(SIZES)} 個尺寸)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
