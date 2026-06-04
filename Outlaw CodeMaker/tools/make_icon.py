"""Generate assets/outlaw.ico — the gold sheriff-star app/desktop icon.

Pure Pillow (no Qt needed), multi-resolution .ico for crisp shortcuts.
Run once:  python tools/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw


SIZE = 256
GOLD = (244, 210, 122, 255)
GOLD_EDGE = (107, 83, 28, 255)
BG = (18, 22, 31, 255)
BORDER = (58, 68, 86, 255)


def _star(cx: float, cy: float, outer: float, inner: float, n: int = 5,
          rot: float = -math.pi / 2) -> list[tuple[float, float]]:
    pts = []
    for i in range(n * 2):
        r = outer if i % 2 == 0 else inner
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=46, fill=BG, outline=BORDER, width=5)
    d.polygon(_star(SIZE / 2, SIZE / 2, SIZE * 0.36, SIZE * 0.16), fill=GOLD, outline=GOLD_EDGE)
    r = SIZE * 0.045
    d.ellipse([SIZE / 2 - r, SIZE / 2 - r, SIZE / 2 + r, SIZE / 2 + r], fill=BG)

    out = Path(__file__).resolve().parent.parent / "assets" / "outlaw.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
