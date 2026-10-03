"""Make tiny Windows report-cat assets from the committed Mac source PNGs.

Matches ReportCatAnimation.swift's display unit and layer widths. Resize each
entire source canvas, including transparent margins; never crop or recenter it.
Pillow is required only to regenerate assets, not to build or run Codexio.
"""

from math import ceil
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "macos" / "Resources" / "ReportCat"
DESTINATION = ROOT / "windows" / "frontend" / "public" / "ReportCat"
WIDTHS = {"head": 82, "page": 88, "left-paw": 26, "right-paw": 26, "tail": 43}
DISPLAY_UNIT = 0.18


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, width in WIDTHS.items():
        with Image.open(SOURCE / f"{name}.png") as source:
            aspect = source.height / source.width
            for scale in (1, 2, 3, 4):
                pixels = ceil(width * DISPLAY_UNIT * scale * max(1, aspect))
                image = source.convert("RGBA")
                image.thumbnail((pixels, pixels), Image.Resampling.LANCZOS)
                target = DESTINATION / f"{name}@{scale}x.png"
                image.save(target, optimize=True)
                total += target.stat().st_size
    print(f"Generated 20 report-cat PNGs, {total:,} bytes total.")


if __name__ == "__main__":
    main()
